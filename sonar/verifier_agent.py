import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from testgen.dotnet import get_dotnet_cmd


@dataclass
class VerificationResult:
    passed: bool
    status: str  # "VERIFIED", "BUILD_ERROR", "TEST_FAILURE", "FILE_ERROR"
    errors: List[str] = field(default_factory=list)
    build_output: str = ""
    test_output: str = ""


class VerifierAgent:
    """
    Verifying Agent: tests refactored C# code using 'dotnet build' and 'dotnet test'
    to ensure 100% build success and zero test regressions.
    """

    def __init__(self, project_or_solution_path: Path):
        self.target_path = Path(project_or_solution_path).resolve()

    def backup_file(self, file_path: Path) -> Path:
        """Creates a .bak backup of the original source file."""
        backup_path = file_path.with_suffix(file_path.suffix + ".bak")
        shutil.copy2(file_path, backup_path)
        return backup_path

    def rollback(self, file_path: Path) -> bool:
        """Restores original file from .bak if backup exists."""
        backup_path = file_path.with_suffix(file_path.suffix + ".bak")
        if backup_path.exists():
            shutil.copy2(backup_path, file_path)
            backup_path.unlink()
            return True
        return False

    def clean_backup(self, file_path: Path) -> None:
        """Deletes .bak once verification succeeds."""
        backup_path = file_path.with_suffix(file_path.suffix + ".bak")
        if backup_path.exists():
            backup_path.unlink()

    def verify(self, run_tests: bool = True) -> VerificationResult:
        """
        Executes 'dotnet build' and 'dotnet test'.
        """
        dotnet_cmd = get_dotnet_cmd()
        # 1. Run dotnet build
        build_cmd = [dotnet_cmd, "build", str(self.target_path), "-v", "minimal"]
        proc_build = subprocess.run(
            build_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )

        build_combined = f"{proc_build.stdout}\n{proc_build.stderr}"
        if proc_build.returncode != 0:
            errors = self._parse_compiler_errors(build_combined)
            return VerificationResult(
                passed=False,
                status="BUILD_ERROR",
                errors=errors,
                build_output=build_combined,
            )

        # 2. Run dotnet test (if tests are enabled and exist)
        if run_tests:
            test_cmd = [dotnet_cmd, "test", str(self.target_path), "--no-build", "-v", "normal"]
            proc_test = subprocess.run(
                test_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )

            test_combined = f"{proc_test.stdout}\n{proc_test.stderr}"
            if proc_test.returncode != 0:
                test_errors = self._parse_test_failures(test_combined)
                return VerificationResult(
                    passed=False,
                    status="TEST_FAILURE",
                    errors=test_errors,
                    build_output=build_combined,
                    test_output=test_combined,
                )

        return VerificationResult(
            passed=True,
            status="VERIFIED",
            build_output=build_combined,
        )

    def _parse_compiler_errors(self, output: str) -> List[str]:
        """Extracts CSxxxx errors from build output."""
        error_lines = []
        for line in output.splitlines():
            line_s = line.strip()
            if "error CS" in line_s or ": error" in line_s:
                error_lines.append(line_s)
        if not error_lines:
            # Fallback to lines containing 'error'
            error_lines = [l.strip() for l in output.splitlines() if "error" in l.lower()][:10]
        return error_lines

    def _parse_test_failures(self, output: str) -> List[str]:
        """Extracts failed test names and assertions from dotnet test output."""
        failures = []
        capture = False
        current_failure = []

        for line in output.splitlines():
            line_s = line.strip()
            if "Failed " in line_s or "[FAIL]" in line_s:
                if current_failure:
                    failures.append("\n".join(current_failure))
                    current_failure = []
                capture = True
                current_failure.append(line_s)
            elif capture:
                if line_s.startswith("Passed ") or line_s.startswith("Total tests:"):
                    capture = False
                    if current_failure:
                        failures.append("\n".join(current_failure))
                        current_failure = []
                else:
                    if len(current_failure) < 8:  # Keep first 8 lines of stacktrace
                        current_failure.append(line_s)

        if current_failure:
            failures.append("\n".join(current_failure))

        if not failures:
            failures = [l.strip() for l in output.splitlines() if "failed" in l.lower()][:5]
        return failures
