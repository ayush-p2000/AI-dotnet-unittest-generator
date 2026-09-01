"""
State and Checkpoint Management for TestGen
===========================================
Tracks file-by-file testing progress, coverage levels, and completion status
so the agent can pause, exit on rate limits, and instantly resume with zero waste.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from testgen.logger import get_logger

DEFAULT_STATE_FILE = ".testgen_state.json"


class StateTracker:
    def __init__(self, state_file: str = DEFAULT_STATE_FILE):
        self.state_file = Path(state_file)
        self.logger = get_logger()
        self.state: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                text = self.state_file.read_text(encoding="utf-8")
                return json.loads(text)
            except Exception as e:
                self.logger.warning(f"[WARNING] Could not read existing checkpoint '{self.state_file}': {e}")
        return {
            "version": "1.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "projects": {},
        }

    def save(self) -> None:
        """
        Atomically saves the checkpoint state to disk.
        Handles Windows read-only attributes and file lock contention.
        """
        import stat
        self.state["last_updated"] = datetime.now(timezone.utc).isoformat()
        state_json = json.dumps(self.state, indent=2)
        temp_file = self.state_file.with_suffix(".tmp")

        if self.state_file.exists():
            try:
                os.chmod(self.state_file, stat.S_IWRITE | stat.S_IREAD)
            except Exception:
                pass

        try:
            temp_file.write_text(state_json, encoding="utf-8")
            temp_file.replace(self.state_file)
        except Exception:
            # Fallback direct write if atomic replace encounters Windows lock
            try:
                self.state_file.write_text(state_json, encoding="utf-8")
            except Exception as e2:
                self.logger.error(f"[ERROR] Failed to save checkpoint state: {e2}")
        finally:
            if temp_file.exists():
                try:
                    temp_file.unlink(missing_ok=True)
                except Exception:
                    pass

                except Exception:
                    pass

    def _get_key(self, project_name: str, file_name: str) -> str:
        return f"{project_name}::{file_name}"

    def get_file_entry(self, project_name: str, file_name: str) -> Optional[Dict[str, Any]]:
        proj = self.state["projects"].get(project_name, {})
        return proj.get(file_name)

    def is_completed(
        self,
        project_name: str,
        file_name: str,
        min_coverage: float = 90.0,
        test_file_path: Optional[Path] = None,
    ) -> bool:
        """Returns True if the file was previously completed with >= min_coverage and test file exists."""
        entry = self.get_file_entry(project_name, file_name)
        if not entry:
            return False

        if entry.get("status") != "SUCCESS":
            return False

        cov = float(entry.get("coverage_pct", 0.0))
        if cov < min_coverage:
            return False

        # If test_file_path is provided, verify it actually exists on disk
        if test_file_path:
            if not test_file_path.exists() or test_file_path.stat().st_size < 50:
                return False
        elif entry.get("test_file"):
            p = Path(entry["test_file"])
            if not p.exists() or p.stat().st_size < 50:
                return False

        return True

    def record_file_result(
        self,
        project_name: str,
        file_name: str,
        result: Dict[str, Any],
    ) -> None:
        """Records the result of a test generation run and immediately saves progress."""
        if project_name not in self.state["projects"]:
            self.state["projects"][project_name] = {}

        self.state["projects"][project_name][file_name] = {
            "status": result.get("status", "UNKNOWN"),
            "coverage_pct": float(result.get("coverage_pct", 0.0)),
            "iterations": int(result.get("iterations", 0)),
            "test_file": result.get("test_file", ""),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        self.save()

    def get_all_results_for_project(self, project_name: str) -> List[Dict[str, Any]]:
        proj = self.state["projects"].get(project_name, {})
        results = []
        for file_name, info in proj.items():
            results.append({
                "file": file_name,
                "test_file": info.get("test_file", ""),
                "status": info.get("status", "UNKNOWN"),
                "coverage_pct": info.get("coverage_pct", 0.0),
                "iterations": info.get("iterations", 0),
            })
        return results

    def get_summary(self, project_name: Optional[str] = None) -> Dict[str, Any]:
        """Returns statistics on completed vs pending files."""
        total = 0
        succeeded = 0
        failed = 0
        total_cov = 0.0

        for p_name, files in self.state["projects"].items():
            if project_name and p_name != project_name:
                continue
            for f_name, info in files.items():
                total += 1
                cov = info.get("coverage_pct", 0.0)
                total_cov += cov
                if info.get("status") == "SUCCESS":
                    succeeded += 1
                else:
                    failed += 1

        avg_cov = total_cov / max(total, 1)
        return {
            "total_tracked": total,
            "succeeded": succeeded,
            "failed_or_partial": failed,
            "avg_coverage_pct": round(avg_cov, 1),
        }
