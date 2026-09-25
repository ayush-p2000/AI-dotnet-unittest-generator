import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from testgen.dotnet import ensure_dotnet_env

# Load local environment & configure dotnet
load_dotenv()
ensure_dotnet_env()

# Global in-memory log buffer
LOG_BUFFER: List[Dict[str, str]] = []
LOG_LOCK = threading.Lock()
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def add_log(msg: str, log_type: str = "info"):
    with LOG_LOCK:
        LOG_BUFFER.append({"msg": str(msg), "type": log_type})
        if len(LOG_BUFFER) > 500:
            LOG_BUFFER.pop(0)


def pop_logs() -> List[Dict[str, str]]:
    with LOG_LOCK:
        lines = list(LOG_BUFFER)
        LOG_BUFFER.clear()
        return lines


def create_job() -> str:
    """Create an in-memory job whose completion can be polled by the UI."""
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "RUNNING"}
    return job_id


def finish_job(job_id: str, result: Dict[str, Any]) -> None:
    """Store a completed job result. Keep only the latest jobs to bound memory."""
    with JOBS_LOCK:
        JOBS[job_id] = result
        if len(JOBS) > 100:
            completed = [key for key, value in JOBS.items() if value.get("status") != "RUNNING"]
            for key in completed[:len(JOBS) - 100]:
                JOBS.pop(key, None)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with JOBS_LOCK:
        result = JOBS.get(job_id)
        return dict(result) if result else None


class StudioHandler(http.server.SimpleHTTPRequestHandler):
    """
    HTTP handler serving UI static assets and REST API endpoints.
    """

    def log_message(self, format, *args):
        # Prevent spamming terminal on every static asset fetch
        pass

    def _send_json(self, data: Any, status_code: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, message: str, status_code: int = 400):
        self._send_json({"error": str(message)}, status_code)

    def _read_json_body(self) -> Dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        raw = self.rfile.read(content_length).decode("utf-8")
        return json.loads(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # 1. API Endpoints
        if path == "/api/health":
            self._send_json({"status": "ok", "time": time.time()})
            return

        elif path == "/api/config":
            self._send_json({
                "project_root": os.getcwd(),
                "sonar_host": os.getenv("SONAR_HOST_URL", "http://localhost:9000"),
                "sonar_token": os.getenv("SONAR_TOKEN", ""),
                "sonar_project_key": os.getenv("SONAR_PROJECT_KEY", ""),
            })
            return

        elif path == "/api/scan-data":
            manifest_path = Path("scan_output.json")
            if manifest_path.exists():
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._send_json(data)
                    return
                except Exception as e:
                    self._send_error(f"Error loading scan_output.json: {e}")
                    return
            self._send_json({"projects": []})
            return

        elif path == "/api/logs":
            self._send_json({"lines": pop_logs()})
            return

        elif path.startswith("/api/jobs/"):
            job = get_job(path.rsplit("/", 1)[-1])
            if job is None:
                self._send_error("Job not found.", 404)
            else:
                self._send_json(job)
            return

        elif path == "/api/providers/models":
            # Detect local Ollama models dynamically
            ollama_models = []
            ollama_available = False
            try:
                import urllib.request
                req = urllib.request.Request("http://localhost:11434/api/tags", headers={"User-Agent": "AI-TestGen"})
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    if resp.status == 200:
                        tags_data = json.loads(resp.read().decode())
                        ollama_models = [m["name"] for m in tags_data.get("models", [])]
                        ollama_available = True
            except Exception:
                ollama_available = False

            if not ollama_models:
                ollama_models = ["qwen3-coder:latest", "qwen2.5-coder:latest"]

            self._send_json({
                "default_provider": os.getenv("AI_PROVIDER", "ollama"),
                "providers": {
                    "ollama": {
                        "name": "Qwen 3 Coder (Local Ollama)",
                        "available": ollama_available,
                        "models": ollama_models,
                        "default_model": "qwen3-coder:latest"
                    },
                    "gemini": {
                        "name": "Google Gemini (AI Studio)",
                        "available": bool(os.getenv("GEMINI_API_KEY")),
                        "models": ["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-2.5-pro"],
                        "default_model": "gemini-3.6-flash"
                    },
                    "qwen-cloud": {
                        "name": "Qwen Cloud (DashScope / Remote)",
                        "available": bool(os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")),
                        "models": ["qwen3-coder-plus", "qwen3-coder-next", "qwen2.5-coder-32b-instruct"],
                        "default_model": "qwen3-coder-plus"
                    }
                }
            })
            return

        # 2. Static Assets Serving
        root_dir = Path(__file__).resolve().parent
        ui_dir = root_dir / "ui"

        if path in ("/", "/index.html"):
            target_file = ui_dir / "index.html"
            content_type = "text/html; charset=utf-8"
        elif path == "/static/index.css":
            target_file = ui_dir / "index.css"
            content_type = "text/css; charset=utf-8"
        elif path == "/static/app.js":
            target_file = ui_dir / "app.js"
            content_type = "application/javascript; charset=utf-8"
        else:
            self.send_error(404, "Not Found")
            return

        if target_file.exists():
            content = target_file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404, "File not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            body = self._read_json_body()
        except Exception as e:
            self._send_error(f"Invalid JSON: {e}")
            return

        # 1. Project Scanner
        if path == "/api/scan":
            target_dir = body.get("path", ".")
            add_log(f"Running scan on '{target_dir}'...", "sys")
            try:
                from testgen.scanner import scan_project
                manifest = scan_project(target_dir)

                # Persist scan_output.json
                out_file = Path("scan_output.json")
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(manifest, f, indent=2)

                add_log(f"Scan complete. Found {len(manifest.get('projects', []))} project(s).", "success")
                self._send_json(manifest)
            except Exception as e:
                add_log(f"Scan failed: {e}", "error")
                self._send_error(str(e), 500)
            return

        # 2. Scaffold Test Project
        elif path == "/api/scaffold":
            proj_name = body.get("project_name")
            if not proj_name:
                self._send_error("Missing project_name")
                return

            try:
                manifest_path = Path("scan_output.json")
                if not manifest_path.exists():
                    self._send_error("No scan_output.json found. Run scan first.")
                    return

                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)

                target_proj = next((p for p in manifest["projects"] if p["project_name"] == proj_name), None)
                if not target_proj:
                    self._send_error(f"Project '{proj_name}' not found in scan manifest.")
                    return

                from testgen.scaffold import scaffold_test_project
                root = manifest["root"]
                test_csproj = scaffold_test_project(root, target_proj["csproj"], target_proj["project_name"])
                add_log(f"Scaffolded test project: {test_csproj}", "success")
                self._send_json({"test_csproj": str(test_csproj)})
            except Exception as e:
                add_log(f"Scaffolding error: {e}", "error")
                self._send_error(str(e), 500)
            return

        # 3. Single File Test Gen
        elif path == "/api/testgen/single":
            file_name = body.get("file_name")
            provider = body.get("provider", "ollama")
            model_name = body.get("model", "qwen3-coder:latest")
            target_cov = float(body.get("coverage", 90.0))
            max_retries = int(body.get("retries", 4))

            add_log(f"Starting test generation for {file_name} (Target: {target_cov}%, Agent: {provider}, Model: {model_name})...", "sys")

            def run_single():
                try:
                    with open("scan_output.json", "r", encoding="utf-8") as f:
                        manifest = json.load(f)

                    from testgen.context import ContextBuilder
                    from testgen.agent_loop import TestGenLoop
                    from testgen.scaffold import scaffold_test_project

                    builder = ContextBuilder(manifest)
                    file_info, project_info = builder.find_file_and_project(file_name)
                    if not file_info or not project_info:
                        raise FileNotFoundError(f"File '{file_name}' is no longer present in the scan manifest.")

                    root = manifest["root"]
                    project_name = project_info["project_name"]
                    test_csproj = (
                        Path(root)
                        / "tests"
                        / f"{project_name}.Tests"
                        / f"{project_name}.Tests.csproj"
                    )
                    if not test_csproj.exists():
                        test_csproj = scaffold_test_project(root, project_info["csproj"], project_name)

                    add_log(f"Using test project: {test_csproj} (exists: {test_csproj.exists()})", "info")

                    loop = TestGenLoop(
                        context_builder=builder,
                        test_csproj=test_csproj,
                        model_name=model_name,
                        target_coverage_pct=target_cov,
                        max_retries=max_retries,
                        provider=provider,
                    )
                    res = loop.process_file(file_info["path"])
                    res["passed"] = res.get("status") == "SUCCESS"
                    add_log(f"Finished {file_name}: {res.get('status')} (Coverage: {res.get('coverage_pct')}%)", "success" if res["passed"] else "warn")
                    finish_job(job_id, res)
                except Exception as ex:
                    add_log(f"Test generation failed: {ex}", "error")
                    finish_job(job_id, {"status": "ERROR", "message": str(ex), "passed": False})

            job_id = create_job()
            t = threading.Thread(target=run_single, daemon=True)
            t.start()
            self._send_json({"status": "RUNNING", "job_id": job_id, "message": f"Test generation started for {file_name}"})
            return

        # 4. Batch Test Gen
        elif path == "/api/testgen/batch":
            proj_name = body.get("project_name", "ALL")
            concurrency = int(body.get("concurrency", 1))
            resume = body.get("resume", True)
            force = body.get("force", False)
            provider = body.get("provider", "ollama")
            model_name = body.get("model", "qwen3-coder:latest")

            add_log(f"Launching batch test generation for project: {proj_name} (Agent: {provider}, Model: {model_name})...", "sys")

            cmd = [
                sys.executable,
                "-u",
                "batch_generate.py",
                "--provider", provider,
                "--model", model_name,
                "--concurrency", str(concurrency),
            ]
            if proj_name != "ALL":
                cmd.extend(["--project", proj_name])
            else:
                cmd.append("--all-projects")
            if resume:
                cmd.append("--resume")
            if force:
                cmd.append("--force")

            def run_batch():
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                for line in iter(proc.stdout.readline, ""):
                    if line.strip():
                        add_log(line.strip(), "info")
                proc.stdout.close()
                proc.wait()
                add_log(f"Batch generation completed with exit code {proc.returncode}.", "success" if proc.returncode == 0 else "warn")

            t = threading.Thread(target=run_batch, daemon=True)
            t.start()
            self._send_json({"status": "RUNNING", "command": " ".join(cmd)})
            return

        # 5. SonarQube Validation
        elif path == "/api/sonar/validate":
            host_url = body.get("host_url", "")
            token = body.get("token", "")
            if host_url.lower() in ("mock", "demo") or "mock" in token.lower():
                self._send_json({
                    "connected": True,
                    "authenticated": True,
                    "server_version": "10.4.1 (Mock Demo)",
                    "status": "UP",
                })
                return

            from sonar.connector import SonarConnector
            connector = SonarConnector(host_url=host_url, token=token)
            val = connector.validate_connection()
            self._send_json(val)
            return

        # 6. SonarQube Projects
        elif path == "/api/sonar/projects":
            host_url = body.get("host_url", "")
            token = body.get("token", "")
            if host_url.lower() in ("mock", "demo") or "mock" in token.lower():
                self._send_json({
                    "projects": [
                        {"key": "MockCommerce", "name": "MockCommerce E-Commerce Service"},
                        {"key": "BillingService", "name": "Billing & Invoicing API"}
                    ]
                })
                return

            from sonar.connector import SonarConnector
            connector = SonarConnector(host_url=host_url, token=token)
            projects = connector.get_projects()
            self._send_json({"projects": [{"key": p.key, "name": p.name} for p in projects]})
            return

        # 7. SonarQube Issues
        elif path == "/api/sonar/issues":
            host_url = body.get("host_url", "")
            token = body.get("token", "")
            project_key = body.get("project_key")
            branch = body.get("branch")

            if host_url.lower() in ("mock", "demo") or "mock" in str(token).lower() or project_key == "MockCommerce":
                self._send_json({
                    "issues": [
                        {
                            "key": "MOCK_S3776_001",
                            "file_path": "MockCommerce/Services/PaymentService.cs",
                            "line": 15,
                            "message": "Refactor this method to reduce its Cognitive Complexity from 26 to the 15 allowed.",
                            "severity": "CRITICAL",
                            "complexity_stats": {"current": 26, "allowed": 15},
                            "flows": [
                                {"line": 28, "msg": "+1 (nesting = 1)"},
                                {"line": 30, "msg": "+2 (nesting = 2)"},
                                {"line": 32, "msg": "+3 (nesting = 3)"},
                                {"line": 36, "msg": "+4 (nesting = 4)"},
                            ],
                        },
                        {
                            "key": "MOCK_S3776_002",
                            "file_path": "MockCommerce/Services/ShippingCalculator.cs",
                            "line": 13,
                            "message": "Refactor this method to reduce its Cognitive Complexity from 22 to the 15 allowed.",
                            "severity": "MAJOR",
                            "complexity_stats": {"current": 22, "allowed": 15},
                            "flows": [
                                {"line": 22, "msg": "+1 (nesting = 1)"},
                                {"line": 24, "msg": "+2 (nesting = 2)"},
                                {"line": 39, "msg": "+2 (nesting = 2)"},
                            ],
                        },
                        {
                            "key": "MOCK_S3776_003",
                            "file_path": "MockCommerce/Services/DiscountEngine.cs",
                            "line": 13,
                            "message": "Refactor this method to reduce its Cognitive Complexity from 20 to the 15 allowed.",
                            "severity": "MAJOR",
                            "complexity_stats": {"current": 20, "allowed": 15},
                            "flows": [
                                {"line": 22, "msg": "+1 (nesting = 1)"},
                                {"line": 24, "msg": "+2 (nesting = 2)"},
                                {"line": 37, "msg": "+2 (nesting = 2)"},
                            ],
                        },
                    ]
                })
                return

            if not project_key:
                self._send_error("Missing project_key")
                return

            from sonar.connector import SonarConnector
            connector = SonarConnector(host_url=host_url, token=token)
            issues = connector.get_cognitive_complexity_issues(project_key=project_key, branch=branch)

            res_issues = []
            for i in issues:
                res_issues.append({
                    "key": i.key,
                    "file_path": i.file_path,
                    "line": i.line,
                    "message": i.message,
                    "severity": i.severity,
                    "complexity_stats": i.complexity_stats,
                    "flows": [{"line": f.line, "msg": f.msg} for f in i.flows],
                })
            self._send_json({"issues": res_issues})
            return

        # 8. SonarQube Auto-Fix / Resolve
        elif path == "/api/sonar/resolve":
            issue_key = body.get("issue_key")
            dry_run = body.get("dry_run", False)
            host_url = body.get("host_url", "")
            token = body.get("token", "")

            from sonar.matcher import IssueMatcher
            from sonar.models import SonarIssue, TextRange

            mock_registry = {
                "MOCK_S3776_001": {
                    "component": "MockCommerce:Services/PaymentService.cs",
                    "line": 13,
                    "msg": "Refactor this method to reduce its Cognitive Complexity from 26 to the 15 allowed.",
                    "start": 13, "end": 70,
                },
                "MOCK_S3776_002": {
                    "component": "MockCommerce:Services/ShippingCalculator.cs",
                    "line": 12,
                    "msg": "Refactor this method to reduce its Cognitive Complexity from 22 to the 15 allowed.",
                    "start": 12, "end": 63,
                },
                "MOCK_S3776_003": {
                    "component": "MockCommerce:Services/DiscountEngine.cs",
                    "line": 12,
                    "msg": "Refactor this method to reduce its Cognitive Complexity from 20 to the 15 allowed.",
                    "start": 12, "end": 68,
                },
            }

            is_mock = issue_key in mock_registry

            if is_mock:
                m = mock_registry[issue_key]
                # For mock issues, root is mock_project/MockCommerce
                target_path = Path("mock_project/MockCommerce").resolve()
                verifier_path = Path("mock_project").resolve()
                issue = SonarIssue(
                    key=issue_key,
                    rule="csharpsquid:S3776",
                    severity="CRITICAL",
                    component=m["component"],
                    project="MockCommerce",
                    line=m["line"],
                    message=m["msg"],
                    status="OPEN",
                    text_range=TextRange(start_line=m["start"], end_line=m["end"]),
                )
            else:
                from sonar.connector import SonarConnector
                connector = SonarConnector(host_url=host_url, token=token)
                issue = connector.get_issue_by_key(issue_key)
                if not issue:
                    self._send_error(f"Issue '{issue_key}' not found on SonarQube.")
                    return
                target_path = Path(".").resolve()
                verifier_path = target_path

            from sonar.refactor_agent import RefactorAgent
            from sonar.verifier_agent import VerifierAgent
            from sonar.agent_loop import SonarRefactorLoop

            matcher = IssueMatcher(target_path)
            # Point verifier at the .sln for proper dotnet build/test
            sln_path = verifier_path / "MockCommerce.sln" if is_mock else verifier_path
            verifier = VerifierAgent(sln_path)
            refactorer = RefactorAgent()
            loop = SonarRefactorLoop(matcher=matcher, verifier_agent=verifier, refactor_agent=refactorer)

            add_log(f"Processing Sonar issue {issue_key} (DryRun: {dry_run})...", "sys")

            def run_resolve():
                try:
                    res = loop.resolve_issue(issue, dry_run=dry_run, run_tests=not is_mock)
                    status = res.get("status", "UNKNOWN")
                    add_log(f"Issue {issue_key} outcome: {status}", "success" if status in ("VERIFIED", "DRY_RUN") else "error")
                    if res.get("refactored_code"):
                        add_log(f"Refactored code preview:\n{res['refactored_code'][:500]}", "info")
                    finish_job(job_id, res)
                except Exception as ex:
                    add_log(f"Resolve failed for {issue_key}: {ex}", "error")
                    finish_job(job_id, {"status": "ERROR", "message": str(ex)})

            job_id = create_job()
            t = threading.Thread(target=run_resolve, daemon=True)
            t.start()
            self._send_json({"status": "RUNNING", "job_id": job_id, "message": f"Resolve started for {issue_key}"})
            return

        else:
            self.send_error(404, "Endpoint not found")


def run_server(port: int = 5000):
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer(("127.0.0.1", port), StudioHandler)
    server.daemon_threads = True
    add_log(f"Nemotron C# Studio Server listening at http://127.0.0.1:{port}", "sys")
    print(f"======================================================================")
    print(f"  NEMOTRON C# STUDIO - WEB UI SERVER RUNNING")
    print(f"  URL: http://127.0.0.1:{port}")
    print(f"======================================================================")
    server.serve_forever()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AI TestGen Studio Server")
    parser.add_argument("--port", type=int, default=5000, help="Port to run server on")
    parser.add_argument("pos_port", nargs="?", type=int, default=None, help="Optional positional port")
    cli_args = parser.parse_args()
    p = cli_args.pos_port or cli_args.port
    run_server(p)
