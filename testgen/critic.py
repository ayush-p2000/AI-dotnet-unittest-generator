import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from testgen.dotnet import get_dotnet_cmd
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

        stem = Path(target_file_name).stem
        dotnet_cmd = get_dotnet_cmd()
        cmd = [
            dotnet_cmd,
            "test",
            str(self.test_csproj),
            "--filter",
            f"FullyQualifiedName~{stem}Test",
            "--logger",
            "trx;LogFileName=test_results.trx",
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

        # If no tests matched the specific filter, retry without filter
        if "No test matches the given testcase filter" in stdout:
            self.logger.info(f"Critic: No tests matched filter '{stem}Test', falling back to full suite run...")
            cmd_unfiltered = [
                dotnet_cmd,
                "test",
                str(self.test_csproj),
                "--logger",
                "trx;LogFileName=test_results.trx",
                "--collect:XPlat Code Coverage",
                "--results-directory",
                str(results_dir),
                "-v",
                "normal",
            ]
            proc = subprocess.run(
                cmd_unfiltered,
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
            feedback = "Compilation failed with the following C# compiler errors:\n"
            feedback += "\n".join(f"- {err}" for err in compile_errors[:15])
            feedback += "\n\nPlease fix ALL compiler errors above. Pay close attention to namespace, type names, and method signatures."
            return {
                "status": "COMPILE_ERROR",
                "passed": False,
                "coverage_pct": 0.0,
                "total_lines": 0,
                "covered_lines": 0,
                "uncovered_lines": [],
                "target_met": False,
                "compile_errors": compile_errors,
                "feedback_message": feedback,
                "raw_output": stdout,
            }

        # 2. Parse Code Coverage from Cobertura XML (Bug 4 fix: parse coverage regardless of test outcome)
        cov_info = self._parse_coverage(results_dir, target_file_name)
        coverage_pct = cov_info["coverage_pct"]
        uncovered_lines = cov_info["uncovered_lines"]
        total_lines = cov_info["total_lines"]
        covered_lines = cov_info["covered_lines"]

        # 3. Check for Test Runtime Failures (TRX first for 100% exact stack traces, then console fallback)
        test_failures = self._parse_trx_failures(results_dir)
        if not test_failures:
            test_failures = self._parse_test_failures(combined_output)

        if test_failures or proc.returncode != 0:
            failure_count = len(test_failures)
            self.logger.warning(f"Critic: Tests failed during execution ({failure_count} failure(s), {coverage_pct:.1f}% coverage)")

            # Build the feedback message — NEVER let it be empty
            if test_failures:
                feedback = f"Tests failed during execution ({failure_count} failure(s), Coverage: {coverage_pct:.1f}%):\n\n"
                formatted_failures = []
                for idx, f in enumerate(test_failures[:10], 1):
                    formatted_failures.append(f"--- [Failure #{idx}] ---\n{f}")
                feedback += "\n\n".join(formatted_failures)
            else:
                # No structured test failures parsed, but build/test failed (returncode != 0).
                # This typically means the build itself failed with errors we didn't catch
                # in _parse_compile_errors (e.g., namespace mismatches, missing references).
                build_errors = self._extract_build_error_summary(combined_output)
                if build_errors:
                    feedback = f"Build/test execution failed (returncode={proc.returncode}, Coverage: {coverage_pct:.1f}%).\n"
                    feedback += "Detected errors from build output:\n"
                    feedback += "\n".join(f"- {err}" for err in build_errors[:15])
                else:
                    # Last resort: include the last N lines of raw output
                    tail_lines = [l.strip() for l in combined_output.splitlines() if l.strip()][-30:]
                    feedback = f"Build/test execution failed (returncode={proc.returncode}, Coverage: {coverage_pct:.1f}%).\n"
                    feedback += "Raw build output (last 30 lines):\n"
                    feedback += "\n".join(tail_lines)

            feedback += "\n\nPlease fix all errors above. Ensure namespaces, type names, and method signatures exactly match the source code provided in context."

            return {
                "status": "TEST_FAILURE",
                "passed": False,
                "coverage_pct": coverage_pct,
                "total_lines": total_lines,
                "covered_lines": covered_lines,
                "target_met": False,
                "test_failures": test_failures,
                "uncovered_lines": uncovered_lines,
                "feedback_message": feedback,
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
            # Match standard C# compiler errors (CS*), NuGet errors (NU*),
            # MSBuild errors (MSB*), and standalone MSBUILD errors
            if (
                ": error CS" in line_str
                or ": error NU" in line_str
                or ": error MSB" in line_str
                or "MSBUILD : error" in line_str
                or ": error FS" in line_str  # F# interop projects
            ):
                errors.append(line_str)
        return errors

    def _parse_trx_failures(self, results_dir: Path) -> List[str]:
        """
        Parses exact, untruncated error messages and complete stack traces
        from Visual Studio Test Results XML (TRX) files.
        """
        failures = []
        trx_files = list(results_dir.glob("**/*.trx"))
        if not trx_files:
            return []

        for trx_file in trx_files:
            try:
                tree = ET.parse(trx_file)
                root = tree.getroot()
                for result in root.iter():
                    if result.tag.endswith("UnitTestResult") and result.attrib.get("outcome") == "Failed":
                        test_name = result.attrib.get("testName", "Unknown Test")
                        error_msg = ""
                        stack_trace = ""

                        for child in result.iter():
                            if child.tag.endswith("Message") and child.text:
                                error_msg = child.text.strip()
                            elif child.tag.endswith("StackTrace") and child.text:
                                stack_trace = child.text.strip()

                        failure_parts = [f"Failed Test: `{test_name}`"]
                        if error_msg:
                            failure_parts.append(f"  Error Message:\n    {error_msg}")
                        if stack_trace:
                            failure_parts.append(f"  Stack Trace:\n    {stack_trace}")

                        failures.append("\n".join(failure_parts))
            except Exception as e:
                self.logger.warning(f"Critic: Failed to parse TRX file {trx_file}: {e}")

        return failures

    def _parse_test_failures(self, output: str) -> List[str]:
        """
        Parses test failures from dotnet test console output.
        Captures the complete error message and full stack trace without arbitrary line limits.
        """
        failures = []
        current_failure = []
        in_failure = False

        for line in output.splitlines():
            stripped = line.strip()
            # Detect start of a failed test in VSTest / xUnit
            if stripped.startswith("Failed ") or (stripped.startswith("Failed:") and not stripped.startswith("Failed: 0")):
                if current_failure:
                    failures.append("\n".join(current_failure))
                    current_failure = []
                in_failure = True
                current_failure.append(stripped)
            elif in_failure:
                # Stop when hitting a new test, summary section, or build status
                if any(stripped.startswith(prefix) for prefix in [
                    "Passed ", "Skipped ", "Test Run ", "Total tests:",
                    "A total of ", "Results File:", "Attachments:",
                    "Build succeeded.", "Build FAILED.", "Time Elapsed",
                    "Passed:", "Failed:", "Skipped:"
                ]):
                    failures.append("\n".join(current_failure))
                    current_failure = []
                    in_failure = False
                elif stripped:
                    current_failure.append("  " + stripped)

        if current_failure:
            failures.append("\n".join(current_failure))

        return failures

    def _extract_build_error_summary(self, output: str) -> List[str]:
        """
        Fallback: when no structured compile errors or test failures were parsed,
        but returncode != 0, extract the most informative error lines from raw output.
        This prevents the author from receiving empty feedback.
        """
        # Known noisy MSBuild/NuGet informational lines that are NOT errors
        noise_patterns = [
            "assets file has not changed",
            "skipping assets file writing",
            "source link is empty",
            "sourcelink.json",
            "determining projects to restore",
            "all projects are up-to-date for restore",
            "nothing to do. none of the projects",
        ]

        error_lines = []
        lines = output.splitlines()
        for line in lines:
            stripped = line.strip()
            # Catch any line containing 'error' in a build-error-like context
            if not stripped:
                continue
            lower = stripped.lower()

            # Skip known benign MSBuild noise before checking markers
            if any(noise in lower for noise in noise_patterns):
                continue

            if any(marker in lower for marker in [
                ": error", "build failed", "not found", "could not",
                "does not exist", "is inaccessible", "no overload",
                "cannot convert", "does not contain", "are you missing",
                "the type or namespace", "ambiguous reference",
                "failed to restore",
            ]):
                error_lines.append(stripped)
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for line in error_lines:
            if line not in seen:
                seen.add(line)
                unique.append(line)
        return unique[:20]

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
