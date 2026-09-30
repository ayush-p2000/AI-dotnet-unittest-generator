"""
testgen.memory
File Iteration Memory Cache for Unit Test Generation & Refinement.
Tracks generation history, detects regressions, provides memory context to Author,
and rolls back poisoned code to the best-known baseline.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field


@dataclass
class IterationRecord:
    iteration: int
    code: str
    status: str
    coverage_pct: float
    passed: bool
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    failures: List[str] = field(default_factory=list)
    compile_errors: List[str] = field(default_factory=list)


class FileIterationMemory:
    """
    In-memory cache of iteration results for a single target file during test generation.
    Maintains historical context, detects regressions, and clears when the file is completed.
    """

    def __init__(self, target_file: str):
        self.target_file = target_file
        self.records: List[IterationRecord] = []

    def record_iteration(
        self,
        iteration: int,
        code: str,
        eval_result: Dict[str, Any],
    ) -> None:
        test_counts = eval_result.get("test_counts", {})
        self.records.append(
            IterationRecord(
                iteration=iteration,
                code=code,
                status=eval_result.get("status", "UNKNOWN"),
                coverage_pct=eval_result.get("coverage_pct", 0.0),
                passed=eval_result.get("passed", False),
                total_tests=test_counts.get("total", 0),
                passed_tests=test_counts.get("passed", 0),
                failed_tests=test_counts.get("failed", 0),
                failures=eval_result.get("test_failures", [])[:5],
                compile_errors=eval_result.get("compile_errors", [])[:5],
            )
        )

    def get_best_record(self) -> Optional[IterationRecord]:
        """Returns the record with highest coverage among 100% passing versions, or highest compiling version."""
        passing = [r for r in self.records if r.passed]
        if passing:
            return max(passing, key=lambda r: (r.coverage_pct, r.passed_tests))
        compiling = [r for r in self.records if r.status != "COMPILE_ERROR"]
        if compiling:
            return max(compiling, key=lambda r: (r.coverage_pct, -r.failed_tests))
        return self.records[-1] if self.records else None

    def get_base_code_for_next_iteration(self, current_code: str) -> str:
        """
        Determines the base code for the next iteration.
        If the latest iteration was a severe regression (e.g., crash, 0% coverage, or compile error
        after an earlier iteration was compiling/passing), rolls back to the best record.
        """
        if len(self.records) < 2:
            return current_code

        latest = self.records[-1]
        best = self.get_best_record()

        if not best:
            return current_code

        # Detect regression:
        # 1. Latest has 0.0% coverage (crashed or aborted) while best had > 0%
        # 2. Latest failed compilation while best compiled
        # 3. Latest has fewer passed tests and lower coverage than best
        is_regression = (
            (latest.coverage_pct == 0.0 and best.coverage_pct > 0.0)
            or (latest.status == "COMPILE_ERROR" and best.status != "COMPILE_ERROR")
            or (latest.passed_tests < best.passed_tests and latest.coverage_pct < best.coverage_pct)
        )

        if is_regression and best.iteration != latest.iteration and best.code:
            return best.code

        return current_code

    def format_memory_prompt(self) -> str:
        """
        Builds a concise summary of previous attempts for the Author prompt.
        Helps the model learn what was already tried, what worked, and what failed.
        """
        if not self.records:
            return ""

        best = self.get_best_record()
        lines = [
            "### ITERATION MEMORY CACHE (Lessons Learned from Previous Attempts):",
            "Review what was previously attempted to avoid repeating past errors:",
        ]

        for r in self.records:
            is_best = " [BEST VERSION]" if best and r.iteration == best.iteration else ""
            summary = (
                f"- Iteration {r.iteration}: Status={r.status}, Coverage={r.coverage_pct:.1f}%, "
                f"Tests={r.passed_tests}/{r.total_tests} passed{is_best}"
            )
            if r.failures:
                first_fail = r.failures[0].splitlines()[0] if r.failures[0] else ""
                summary += f" | Issue: {first_fail[:100]}"
            elif r.compile_errors:
                first_err = r.compile_errors[0] if r.compile_errors else ""
                summary += f" | Compiler: {first_err[:100]}"
            lines.append(summary)

        if len(self.records) >= 2:
            latest = self.records[-1]
            if best and latest.iteration != best.iteration and (latest.coverage_pct < best.coverage_pct or latest.status == "COMPILE_ERROR"):
                lines.append(
                    f"\n[REGRESSION NOTICE] Iteration {latest.iteration} caused a regression. "
                    f"The base code below has been ROLLED BACK to Iteration {best.iteration} "
                    f"({best.coverage_pct:.1f}% coverage). Build upon the working Iteration {best.iteration} code "
                    f"and do NOT repeat the modifications that caused Iteration {latest.iteration} to fail."
                )

        return "\n".join(lines)

    def clear(self) -> None:
        """Clears the iteration cache once the file generation completes."""
        self.records.clear()
