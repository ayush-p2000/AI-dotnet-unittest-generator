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
            f"FullyQualifiedName~{stem}",
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

        executed_cmd = " ".join(cmd)
        # If no tests matched the specific filter, retry without filter
        filter_miss = (
            "No test matches the given testcase filter" in stdout
            or "No test matches the given testcase filter" in stderr
        )
        if filter_miss:
            self.logger.info(f"Critic: No tests matched filter '{stem}', falling back to full suite run...")
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
            executed_cmd = " ".join(cmd_unfiltered)
            proc = subprocess.run(
                cmd_unfiltered,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            stdout = proc.stdout
            stderr = proc.stderr

        combined_output = f"{stdout}\n{stderr}".strip()
        test_counts = self._parse_test_counts(results_dir, combined_output)

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
                "test_counts": test_counts,
                "compile_errors": compile_errors,
                "feedback_message": feedback,
                "raw_output": combined_output,
                "command": executed_cmd,
                "test_project": str(self.test_csproj),
            }

        # 2. Parse Code Coverage from Cobertura XML (Bug 4 fix: parse coverage regardless of test outcome)
        cov_info = self._parse_coverage(results_dir, target_file_name)
        coverage_pct = cov_info["coverage_pct"]
        uncovered_lines = cov_info["uncovered_lines"]
        total_lines = cov_info["total_lines"]
        covered_lines = cov_info["covered_lines"]

        # 3. Check for Test Runtime Failures (TRX first for 100% exact stack traces, then console fallback)
        test_failures = []
        if test_counts.get("failed", 0) > 0 or proc.returncode != 0:
            test_failures = self._parse_trx_failures(results_dir)
            if not test_failures and (test_counts.get("failed", 0) > 0 or proc.returncode != 0):
                test_failures = self._parse_test_failures(combined_output)

        # Detect any CLR crashes or unhandled exceptions (e.g., GC finalizer crashes, test host aborts)
        crashes = self._parse_crash_diagnostics(combined_output)
        if crashes:
            # Add crash diagnostic to failures if not already captured
            for crash in crashes:
                if not any(crash in tf for tf in test_failures):
                    test_failures.append(crash)

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
                "test_counts": test_counts,
                "test_failures": test_failures,
                "uncovered_lines": uncovered_lines,
                "feedback_message": feedback,
                "raw_output": combined_output,
                "command": executed_cmd,
                "test_project": str(self.test_csproj),
            }

        # 4. Check if 0 tests ran
        if test_counts.get("total", 0) == 0:
            self.logger.warning(f"Critic: 0 tests were executed for {target_file_name}")
            return {
                "status": "NO_TESTS",
                "passed": False,
                "coverage_pct": 0.0,
                "total_lines": total_lines,
                "covered_lines": covered_lines,
                "uncovered_lines": uncovered_lines,
                "target_met": False,
                "test_counts": test_counts,
                "test_failures": [],
                "feedback_message": (
                    f"No tests were discovered or executed for `{target_file_name}`.\n"
                    "Please ensure:\n"
                    "1. The test class is public (`public class <Target>Test`).\n"
                    "2. Test methods are decorated with `[Fact]` or `[Theory]`.\n"
                    "3. The test class does not throw in its constructor or static initializer."
                ),
                "raw_output": combined_output,
                "command": executed_cmd,
                "test_project": str(self.test_csproj),
            }

        # 5. Check coverage target
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
                "test_counts": test_counts,
                "uncovered_lines": uncovered_lines,
                "feedback_message": feedback,
                "raw_output": combined_output,
                "command": executed_cmd,
                "test_project": str(self.test_csproj),
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
            "test_counts": test_counts,
            "uncovered_lines": uncovered_lines,
            "feedback_message": f"SUCCESS! All tests passed with {coverage_pct:.1f}% code coverage.",
            "raw_output": combined_output,
            "command": executed_cmd,
            "test_project": str(self.test_csproj),
        }

    def _parse_test_counts(self, results_dir: Path, output: str) -> Dict[str, Any]:
        """
        Parses total, passed, failed, and skipped test counts from TRX files
        or console output.
        """
        counts = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "duration_s": 0.0}
        trx_files = list(results_dir.glob("**/*.trx"))
        if trx_files:
            for trx_file in trx_files:
                try:
                    tree = ET.parse(trx_file)
                    root = tree.getroot()
                    for elem in root.iter():
                        if elem.tag.endswith("Counters"):
                            counts["total"] = int(elem.attrib.get("total", "0"))
                            counts["passed"] = int(elem.attrib.get("passed", "0"))
                            counts["failed"] = (
                                int(elem.attrib.get("failed", "0"))
                                + int(elem.attrib.get("error", "0"))
                                + int(elem.attrib.get("timeout", "0"))
                            )
                            counts["skipped"] = (
                                int(elem.attrib.get("notExecuted", "0"))
                                + int(elem.attrib.get("inconclusive", "0"))
                            )
                            break
                except Exception:
                    pass

        # Fallback to console parsing if TRX had 0 total
        if counts["total"] == 0:
            total_m = re.search(r"Total tests:\s*(\d+)", output, re.IGNORECASE)
            passed_m = re.search(r"Passed:\s*(\d+)", output, re.IGNORECASE)
            failed_m = re.search(r"Failed:\s*(\d+)", output, re.IGNORECASE)
            skipped_m = re.search(r"Skipped:\s*(\d+)", output, re.IGNORECASE)

            if total_m:
                counts["total"] = int(total_m.group(1))
            if passed_m:
                counts["passed"] = int(passed_m.group(1))
            if failed_m:
                counts["failed"] = int(failed_m.group(1))
            if skipped_m:
                counts["skipped"] = int(skipped_m.group(1))

        # Check duration
        dur_m = re.search(r"Duration:\s*([\d\.]+)\s*(ms|s|m)", output, re.IGNORECASE)
        if dur_m:
            val = float(dur_m.group(1))
            unit = dur_m.group(2).lower()
            if unit == "ms":
                counts["duration_s"] = val / 1000.0
            elif unit == "m":
                counts["duration_s"] = val * 60.0
            else:
                counts["duration_s"] = val

        return counts

    def _parse_compile_errors(self, output: str) -> List[str]:
        errors = []
        seen = set()
        for line in output.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            # Match standard C# compiler errors (CS*), NuGet errors (NU*),
            # MSBuild errors (MSB*), NETSDK errors, and standalone tool errors
            if (
                ": error CS" in line_str
                or ": error NU" in line_str
                or ": error MSB" in line_str
                or ": error NETSDK" in line_str
                or ": error FS" in line_str
                or ": error IL" in line_str
                or "MSBUILD : error" in line_str
                or "EXEC : error" in line_str
                or ": fatal error" in line_str
                or re.search(r":\s*(?:fatal\s+)?error\s+[A-Za-z0-9_-]+:", line_str, re.IGNORECASE)
            ):
                if line_str not in seen:
                    seen.add(line_str)
                    errors.append(line_str)
        return errors

    def _parse_trx_failures(self, results_dir: Path) -> List[str]:
        """
        Parses exact, untruncated error messages and complete stack traces
        from Visual Studio Test Results XML (TRX) files.
        """
        failures = []
        trx_files = sorted(results_dir.glob("**/*.trx"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not trx_files:
            return []

        for trx_file in trx_files:
            try:
                tree = ET.parse(trx_file)
                root = tree.getroot()
                for result in root.iter():
                    outcome = result.attrib.get("outcome", "")
                    if result.tag.endswith("UnitTestResult") and outcome in ("Failed", "Error", "Timeout", "Aborted"):
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
                            # Actionable diagnostic fix suggestions
                            if "ElectronExecutable" in error_msg or "TypeInitializationException" in error_msg:
                                failure_parts.append("  [FIX ADVICE] Electron.NET runtime initialization failed. Add a static constructor to your test class to initialize ElectronTestAssembly:\n"
                                                     "    static <TestClass>() { AppDomain.CurrentDomain.SetData(\"ElectronTestAssembly\", typeof(<TargetClass>).Assembly); }")
                            elif "Extension methods" in error_msg and "may not be used in setup" in error_msg:
                                failure_parts.append("  [FIX ADVICE] Moq cannot mock static C# extension methods! Check the interface definition in Context and mock the underlying declared interface method instead.")
                            elif "Expected result.Success to be False, but found True" in error_msg or "no exception was thrown" in error_msg:
                                failure_parts.append("  [FIX ADVICE] The method fell through to default success. Verify that your Moq setup matches the EXACT parameter count and overload called by the source class.")
                            elif "MockBehavior.Strict" in error_msg or "All invocations on the mock must have a corresponding setup" in error_msg:
                                failure_parts.append("  [FIX ADVICE] Strict mock invoked without setup. Either use default Loose behavior (`new Mock<T>()`) or configure setups for all called members.")
                            elif "NullReferenceException" in error_msg:
                                failure_parts.append("  [FIX ADVICE] NullReferenceException encountered. Verify all constructor dependencies, service mocks (.Object), and required DTO properties are initialized.")
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
            # Detect start of a failed test in VSTest / xUnit (excluding MSBuild messages like 'Failed to load')
            if (
                (stripped.startswith("Failed ") and not stripped.startswith("Failed to "))
                or (stripped.startswith("Failed:") and not stripped.startswith("Failed: 0"))
                or "[FAIL]" in stripped
            ):
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

    def _parse_crash_diagnostics(self, output: str) -> List[str]:
        """
        Detects and parses test host process crashes, fatal errors, and unhandled exceptions
        (e.g., exceptions thrown on finalizer threads, StackOverflowException, etc.).
        """
        crashes = []
        lines = output.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            # Look for crash signatures
            if (
                line.startswith("Unhandled exception")
                or "Test host process crashed" in line
                or "The active test run was aborted" in line
                or line.startswith("Fatal error")
                or line.startswith("Process terminated")
            ):
                crash_lines = [line]
                i += 1
                while i < len(lines):
                    next_line = lines[i].strip()
                    if not next_line:
                        # Allow single blank line in stack trace if followed by stack frame
                        if i + 1 < len(lines) and lines[i + 1].strip().startswith("at "):
                            i += 1
                            continue
                        else:
                            break
                    # Keep collecting stack trace lines
                    if (
                        next_line.startswith("at ")
                        or next_line.startswith("---")
                        or "Reason:" in next_line
                        or "exception" in next_line.lower()
                        or "Test Run Aborted" in next_line
                    ):
                        crash_lines.append(next_line)
                        i += 1
                    else:
                        break
                crash_text = "\n".join(crash_lines)

                # Check for GC finalizer crash
                if "GC.RunFinalizers" in crash_text or "Component.Finalize" in crash_text or "Finalize()" in crash_text:
                    crash_text += (
                        "\n\n[CRITICAL FIX ADVICE - CLR TEST HOST CRASH]:\n"
                        "The test host crashed because an unhandled exception was thrown on the CLR Finalizer thread\n"
                        "during garbage collection (System.GC.RunFinalizers / Component.Finalize).\n"
                        "In .NET, any unhandled exception in a finalizer immediately terminates the entire test process.\n"
                        "- Do NOT throw exceptions inside Dispose(bool disposing) when disposing == false.\n"
                        "- If mocking or subclassing IDisposable or Component (e.g. Process) to simulate a failure,\n"
                        "  only throw if (disposing) is true, AND call `GC.SuppressFinalize(this)` in the constructor\n"
                        "  so the garbage collector never runs the finalizer."
                    )
                crashes.append(crash_text)
            else:
                i += 1
        return crashes

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
                "failed to restore", "unhandled exception",
                "active test run was aborted", "test host process crashed",
                "runfinalizers",
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
        Parses Cobertura XML coverage results for the target file.
        Deduplicates lines across nested classes, closures, and async state machines
        to ensure 100% mathematically exact coverage numbers.
        """
        cobertura_files = sorted(results_dir.glob("**/coverage.cobertura.xml"), key=lambda p: p.stat().st_mtime, reverse=True)
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

            line_hits: Dict[int, int] = {}

            # Search for classes matching the target file
            for cls in root.findall(".//class"):
                filename = cls.get("filename", "").replace("\\", "/").lower()
                cls_name = cls.get("name", "").lower()

                # Exact filename match or exact class name match (including async/nested classes)
                filename_matches = (
                    filename.endswith(f"/{target_exact_filename}")
                    or filename == target_exact_filename
                    or Path(filename).name == target_exact_filename
                )
                cls_matches = (
                    cls_name == target_base
                    or cls_name.endswith(f".{target_base}")
                    or f".{target_base}/" in cls_name
                    or f".{target_base}+" in cls_name
                    or f".{target_base}`" in cls_name
                )

                if filename_matches or cls_matches:
                    for line_elem in cls.findall(".//line"):
                        line_num = int(line_elem.get("number", "0"))
                        hits = int(line_elem.get("hits", "0"))
                        if line_num > 0:
                            # Aggregate maximum hits for this line across all methods/state machines
                            line_hits[line_num] = max(line_hits.get(line_num, 0), hits)

            if line_hits:
                total_lines = len(line_hits)
                covered_lines = sum(1 for hits in line_hits.values() if hits > 0)
                uncovered_lines = sorted(l_num for l_num, hits in line_hits.items() if hits == 0)
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
