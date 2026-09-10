"""
Unit & Integration tests for SonarConnector.
Supports running in mock mode (--mock) without requiring a live SonarQube instance,
as well as running against a live SonarQube instance configured in .env or via CLI.
"""

import argparse
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path
from typing import Optional

# Ensure project root is in sys.path when script is executed directly
current_dir = Path(__file__).resolve().parent
parent_dir = current_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

from sonar.connector import SonarConnector, SonarAuthenticationError, SonarConnectionError
from sonar.models import SonarIssue, TextRange


class MockSonarHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Quiet down server logs during testing

    def do_GET(self):
        auth_header = self.headers.get("Authorization", "")
        # Check basic auth: Expects token:
        if not auth_header.startswith("Basic "):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"errors": [{"msg": "Unauthorized"}]}).encode("utf-8"))
            return

        if "/api/system/status" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "id": "20260910-SQ",
                "version": "10.4.1",
                "status": "UP"
            }).encode("utf-8"))

        elif "/api/authentication/validate" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"valid": True}).encode("utf-8"))

        elif "/api/projects/search" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "paging": {"pageIndex": 1, "pageSize": 100, "total": 1},
                "components": [
                    {
                        "key": "sample-dotnet-project",
                        "name": "Sample Dotnet Project",
                        "qualifier": "TRK",
                        "visibility": "public"
                    }
                ]
            }).encode("utf-8"))

        elif "/api/issues/search" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "total": 1,
                "p": 1,
                "ps": 100,
                "paging": {"pageIndex": 1, "pageSize": 100, "total": 1},
                "issues": [
                    {
                        "key": "AY_TEST_ISSUE_001",
                        "rule": "csharpsquid:S3776",
                        "severity": "CRITICAL",
                        "component": "sample-dotnet-project:Services/PaymentProcessor.cs",
                        "project": "sample-dotnet-project",
                        "line": 42,
                        "message": "Refactor this method to reduce its Cognitive Complexity from 28 to the 15 allowed.",
                        "status": "OPEN",
                        "effort": "25min",
                        "debt": "25min",
                        "textRange": {
                            "startLine": 42,
                            "endLine": 110,
                            "startOffset": 4,
                            "endOffset": 5
                        },
                        "flows": [
                            {
                                "locations": [
                                    {
                                        "msg": "+1 (nesting = 1)",
                                        "textRange": {"startLine": 50, "endLine": 50, "startOffset": 8, "endOffset": 12}
                                    },
                                    {
                                        "msg": "+2 (nesting = 2)",
                                        "textRange": {"startLine": 65, "endLine": 65, "startOffset": 12, "endOffset": 16}
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }).encode("utf-8"))

        elif "/api/qualitygates/project_status" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "projectStatus": {
                    "status": "ERROR",
                    "conditions": [
                        {
                            "status": "ERROR",
                            "metricKey": "cognitive_complexity",
                            "actualValue": "28"
                        }
                    ]
                }
            }).encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()


def run_mock_tests() -> bool:
    """Spins up a lightweight local HTTP server and validates SonarConnector."""
    server = socketserver.TCPServer(("127.0.0.1", 0), MockSonarHandler)
    port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{port}"
    print(f"[TEST] Mock SonarQube Server started at {base_url}")

    try:
        # 1. Test unauthenticated failure
        unauth_client = SonarConnector(host_url=base_url, token="")
        try:
            unauth_client.get_projects()
            print("FAILED: Expected authentication error for empty token")
            return False
        except SonarAuthenticationError:
            print("PASS: Unauthenticated request rejected with HTTP 401")

        # 2. Test valid connection & auth
        client = SonarConnector(host_url=base_url, token="test_token_12345")
        val = client.validate_connection()
        assert val["connected"] is True, f"Connection validation failed: {val}"
        assert val["authenticated"] is True, f"Auth validation failed: {val}"
        assert val["server_version"] == "10.4.1", f"Version mismatch: {val}"
        print(f"PASS: validate_connection() -> connected, version {val['server_version']}")

        # 3. Test project search
        projects = client.get_projects()
        assert len(projects) == 1, f"Expected 1 project, got {len(projects)}"
        assert projects[0].key == "sample-dotnet-project"
        print(f"PASS: get_projects() -> found '{projects[0].name}' ({projects[0].key})")

        # 4. Test cognitive complexity issues
        issues = client.get_cognitive_complexity_issues(project_key="sample-dotnet-project")
        assert len(issues) == 1, f"Expected 1 issue, got {len(issues)}"
        issue = issues[0]
        assert issue.key == "AY_TEST_ISSUE_001"
        assert issue.rule == "csharpsquid:S3776"
        assert issue.file_path == "Services/PaymentProcessor.cs"
        assert issue.line == 42
        assert issue.text_range.start_line == 42
        assert issue.text_range.end_line == 110

        stats = issue.complexity_stats
        assert stats["current"] == 28, f"Parsed stats: {stats}"
        assert stats["allowed"] == 15, f"Parsed stats: {stats}"
        assert len(issue.flows) == 2, f"Expected 2 flows, got {len(issue.flows)}"
        print(f"PASS: get_cognitive_complexity_issues() -> issue {issue.key} parsed accurately:")
        print(f"      File: {issue.file_path} (Lines {issue.text_range.start_line}-{issue.text_range.end_line})")
        print(f"      Complexity: {stats['current']} (allowed threshold: {stats['allowed']})")
        print(f"      Complexity Flows: {len(issue.flows)} hotspot locations detected")

        # 5. Test Quality Gate
        gate = client.get_project_quality_gate("sample-dotnet-project")
        assert gate["projectStatus"]["status"] == "ERROR"
        print("PASS: get_project_quality_gate() -> verified gate status response")

        print("\n==========================================")
        print(" ALL SONAR CONNECTOR MOCK TESTS PASSED! ")
        print("==========================================")
        return True

    finally:
        server.shutdown()
        server.server_close()


def run_live_tests(host: Optional[str], token: Optional[str], project: Optional[str]):
    """Connects to a live SonarQube / SonarCloud server."""
    client = SonarConnector(host_url=host, token=token)
    print(f"Connecting to SonarQube at {client.host_url}...")
    val = client.validate_connection()
    print(f"Connection Status: {val}")

    if not val.get("connected"):
        print("Could not connect to host. Check host URL.")
        return

    if not val.get("authenticated"):
        print("Warning: Token was not authenticated. Check SONAR_TOKEN.")

    try:
        projects = client.get_projects()
        print(f"\nFound {len(projects)} project(s):")
        for p in projects[:5]:
            print(f"  - {p.name} [{p.key}]")

        target_project = project or (projects[0].key if projects else None)
        if target_project:
            print(f"\nFetching Cognitive Complexity issues for '{target_project}'...")
            issues = client.get_cognitive_complexity_issues(project_key=target_project)
            print(f"Found {len(issues)} Cognitive Complexity (S3776) issue(s):")
            for i in issues:
                stats = i.complexity_stats
                print(f"  - [{i.severity}] {i.file_path}:{i.line} -> Complexity {stats['current']} (Limit: {stats['allowed']})")
                print(f"    Message: {i.message}")
                for flow in i.flows[:3]:
                    print(f"      * Line {flow.line}: {flow.msg}")
    except Exception as e:
        print(f"Error communicating with live SonarQube: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test SonarQube Connector")
    parser.add_argument("--mock", action="store_true", help="Run automated mock suite against local test server")
    parser.add_argument("--host", help="SonarQube host URL")
    parser.add_argument("--token", help="SonarQube token")
    parser.add_argument("--project", help="SonarQube project key")

    args = parser.parse_args()

    if args.mock or (not args.host and not args.token and not args.project):
        success = run_mock_tests()
        sys.exit(0 if success else 1)
    else:
        run_live_tests(args.host, args.token, args.project)
