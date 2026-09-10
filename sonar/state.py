import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_STATE_FILE = ".sonar_complexity_state.json"


class SonarStateTracker:
    """
    Persists progress and resolution history for SonarQube issues across runs.
    """

    def __init__(self, state_file: str = DEFAULT_STATE_FILE):
        self.state_file = Path(state_file).resolve()
        self.state = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "version": "1.0",
            "last_run": None,
            "issues": {},
        }

    def save(self) -> None:
        self.state["last_run"] = datetime.now(timezone.utc).isoformat()
        tmp = self.state_file.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)
        tmp.replace(self.state_file)

    def is_resolved(self, issue_key: str) -> bool:
        entry = self.state.get("issues", {}).get(issue_key)
        return bool(entry and entry.get("status") == "VERIFIED")

    def record_issue_result(
        self,
        issue_key: str,
        file_path: str,
        method_name: str,
        status: str,
        initial_complexity: int,
        allowed_complexity: int,
        attempts: int,
        error_summary: Optional[str] = None,
    ) -> None:
        self.state["issues"][issue_key] = {
            "file": file_path,
            "method": method_name,
            "status": status,
            "initial_complexity": initial_complexity,
            "allowed_complexity": allowed_complexity,
            "attempts": attempts,
            "error_summary": error_summary,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        self.save()

    def get_summary(self) -> Dict[str, Any]:
        issues = self.state.get("issues", {})
        total = len(issues)
        verified = sum(1 for i in issues.values() if i.get("status") == "VERIFIED")
        failed = sum(1 for i in issues.values() if i.get("status") in ("BUILD_ERROR", "TEST_FAILURE", "EXHAUSTED"))
        return {
            "total_processed": total,
            "verified": verified,
            "failed": failed,
        }
