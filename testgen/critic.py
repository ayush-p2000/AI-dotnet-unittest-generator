import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from testgen.logger import get_logger


class CriticAgent:
    def __init__(self, test_csproj: Path, target_coverage_pct: float = 90.0):
        self.test_csproj = Path(test_csproj).resolve()
        self.target_coverage_pct = target_coverage_pct
        self.logger = get_logger()

    def run_tests_and_evaluate(
        self,
        target_file_name: str,
        results_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Executes dotnet test with coverage, parses compilation errors,
        test failures, and line coverage for the target file.
        """
        if results_dir is None:
            results_dir = self.test_csproj.parent / "TestResults"

        # Clean previous coverage results
        if results_dir.exists():
            shutil.rmtree(results_dir, ignore_errors=True)
        results_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "dotnet",
            "test",
            str(self.test_csproj),
            "--collect:XPlat Code Coverage",
            "--results-directory",
            str(results_dir),
            "-v",
            "normal",
        ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )

        stdout = proc.stdout
        stderr = proc.stderr
        combined_output = f"{stdout}\n{stderr}"

        # 1. Check for Compilation / Build Errors
        compile_errors = self._parse_compile_errors(combined_output)
        if compile_errors:
            self.logger.warning(f"Critic: Compilation failed with {len(compile_errors)} error(s)")
            return {
                "status": "COMPILE_ERROR",
                "passed": False,
                "coverage_pct": 0.0,
                "total_lines": 0,
                "covered_lines": 0,
                "uncovered_lines": [],
                "target_met": False,
                "compile_errors": compile_errors,
                "feedback_message": "Compilation failed with the following C# compiler errors:\n"
                + "\n".join(f"- {err}" for err in compile_errors[:10]),
                "raw_output": stdout,
            }

        # 2. Parse Code Coverage from Cobertura XML (Bug 4 fix: parse coverage regardless of test outcome)
        cov_info = self._parse_coverage(results_dir, target_file_name)
        coverage_pct = cov_info["coverage_pct"]
        uncovered_lines = cov_info["uncovered_lines"]
        total_lines = cov_info["total_lines"]
        covered_lines = cov_info["covered_lines"]

        # 3. Check for Test Runtime Failures
        test_failures = self._parse_test_failures(combined_output)
        if test_failures or proc.returncode != 0:
            self.logger.warning(f"Critic: Tests failed during execution ({len(test_failures)} failure(s), {coverage_pct:.1f}% coverage)")
            return {
                "status": "TEST_FAILURE",
                "passed": False,
                "coverage_pct": coverage_pct,
                "total_lines": total_lines,
                "covered_lines": covered_lines,
                "target_met": False,
                "test_failures": test_failures,
                "uncovered_lines": uncovered_lines,
                "feedback_message": f"Tests failed during execution (Coverage: {coverage_pct:.1f}%):\n"
                + "\n".join(f"- {f}" for f in test_failures[:10]),
                "raw_output": stdout,
            }

        # 4. Check coverage target
        target_met = coverage_pct >= self.target_coverage_pct

        if not target_met:
            self.logger.info(f"Critic: All tests passed, but coverage {coverage_pct:.1f}% < {self.target_coverage_pct}%")
            feedback = (
                f"All tests passed, but code coverage is {coverage_pct:.1f}% "
                f"(Target is >={self.target_coverage_pct}%).\n"
            )
            if uncovered_lines:
                feedback += f"Uncovered line numbers in `{target_file_name}`: {', '.join(map(str, uncovered_lines[:25]))}.\n"
            feedback += "Please add more tests covering untested branches, edge cases, or exception paths to reach >= 90%."

            return {
                "status": "LOW_COVERAGE",
                "passed": True,
                "coverage_pct": coverage_pct,
                "total_lines": total_lines,
                "covered_lines": covered_lines,
                "target_met": False,
                "uncovered_lines": uncovered_lines,
                "feedback_message": feedback,
                "raw_output": stdout,
            }

        # 5. Success - Tests passed and coverage >= 90%
        self.logger.info(f"Critic: SUCCESS! {coverage_pct:.1f}% coverage ({covered_lines}/{total_lines} lines covered)")
        return {
            "status": "SUCCESS",
            "passed": True,
            "coverage_pct": coverage_pct,
            "total_lines": total_lines,
            "covered_lines": covered_lines,
            "target_met": True,
            "uncovered_lines": uncovered_lines,
            "feedback_message": f"SUCCESS! All tests passed with {coverage_pct:.1f}% code coverage.",
            "raw_output": stdout,
        }

    def _parse_compile_errors(self, output: str) -> List[str]:
        errors = []
        for line in output.splitlines():
            line_str = line.strip()
            if ": error CS" in line_str or ": error NU" in line_str:
                errors.append(line_str)
        return errors

    def _parse_test_failures(self, output: str) -> List[str]:
        failures = []
        lines = output.splitlines()
        for i, line in enumerate(lines):
            line_str = line.strip()
            if line_str.startswith("Failed") or "Error Message:" in line_str:
                failures.append(line_str)
                # Grab the next line if it contains the assertion message
                if i + 1 < len(lines) and lines[i + 1].strip():
                    failures.append(lines[i + 1].strip())
        return failures

    def _parse_coverage(self, results_dir: Path, target_file_name: str) -> Dict[str, Any]:
        """
        Bug 12 fix: Tightens class matching to exact filename or exact class name
        to avoid false positive substring matches (e.g. Service.cs matching BookingsService).
        """
        cobertura_files = list(results_dir.glob("**/coverage.cobertura.xml"))
        if not cobertura_files:
            return {
                "coverage_pct": 0.0,
                "total_lines": 0,
                "covered_lines": 0,
                "uncovered_lines": [],
            }

        try:
            tree = ET.parse(cobertura_files[0])
            root = tree.getroot()

            target_base = Path(target_file_name).stem.lower()
            target_exact_filename = Path(target_file_name).name.lower()

            uncovered_lines: List[int] = []
            total_lines = 0
            covered_lines = 0

            # Search for classes matching the target file
            for cls in root.findall(".//class"):
                filename = cls.get("filename", "").replace("\\", "/").lower()
                cls_name = cls.get("name", "").lower()

                # Exact filename match or exact class name match
                filename_matches = (
                    filename.endswith(f"/{target_exact_filename}")
                    or filename == target_exact_filename
                    or Path(filename).name == target_exact_filename
                )
                cls_matches = (
                    cls_name == target_base
                    or cls_name.endswith(f".{target_base}")
                )

                if filename_matches or cls_matches:
                    for line_elem in cls.findall(".//line"):
                        total_lines += 1
                        hits = int(line_elem.get("hits", "0"))
                        line_num = int(line_elem.get("number", "0"))
                        if hits > 0:
                            covered_lines += 1
                        else:
                            uncovered_lines.append(line_num)

            if total_lines > 0:
                pct = (covered_lines / total_lines) * 100.0
                return {
                    "coverage_pct": pct,
                    "total_lines": total_lines,
                    "covered_lines": covered_lines,
                    "uncovered_lines": uncovered_lines,
                }

            # Fallback to total project coverage if specific class was not found in XML
            total_line_rate = float(root.get("line-rate", "0.0"))
            return {
                "coverage_pct": total_line_rate * 100.0,
                "total_lines": 0,
                "covered_lines": 0,
                "uncovered_lines": [],
            }

        except Exception as e:
            self.logger.warning(f"Failed to parse coverage XML: {e}")
            return {
                "coverage_pct": 0.0,
                "total_lines": 0,
                "covered_lines": 0,
                "uncovered_lines": [],
            }
