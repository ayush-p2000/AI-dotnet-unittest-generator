import time
from pathlib import Path
from typing import Any, Dict, Optional

from testgen.author import AuthorAgent, AllKeysRateLimitedError
from testgen.context import ContextBuilder
from testgen.critic import CriticAgent
from testgen.logger import get_logger, log_critic_evaluation, log_file_final_summary
from testgen.memory import FileIterationMemory


class TestGenLoop:
    def __init__(
        self,
        context_builder: ContextBuilder,
        test_csproj: Path,
        model_name: Optional[str] = None,
        target_coverage_pct: float = 90.0,
        max_retries: int = 4,
        author_agent: Optional[AuthorAgent] = None,  # Bug 14 fix: allow sharing AuthorAgent
        provider: Optional[str] = None,
    ):
        self.context_builder = context_builder
        self.test_csproj = Path(test_csproj).resolve()
        self.author = author_agent or AuthorAgent(model_name=model_name, provider=provider)
        self.critic = CriticAgent(
            test_csproj=self.test_csproj,
            target_coverage_pct=target_coverage_pct,
        )
        self.max_retries = max_retries
        self.target_coverage_pct = target_coverage_pct
        self.logger = get_logger()

    def get_test_file_path(self, target_file_name: str, sub_folder: str = "") -> Path:
        """Mirror the directory structure of the target file inside the test project, ending with Test.cs."""
        test_dir = self.test_csproj.parent
        destination_dir = (test_dir / sub_folder) if sub_folder else test_dir
        destination_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(target_file_name).stem
        return destination_dir / f"{stem}Test.cs"

    def process_file(self, target_file_name_or_path: str) -> Dict[str, Any]:
        """
        Runs the smart 2-agent loop for a single source file:
        1. If a test file already exists:
           - Evaluates with Critic Agent.
           - If coverage >= 90% and all tests pass -> Returns SUCCESS immediately (no LLM calls).
           - If tests fail or coverage < 90% -> Uses existing code & coverage gaps as feedback for Author Agent.
        2. If no test file exists:
           - Generates test cases from scratch.
        3. Loops up to max_retries to reach >= 90% coverage.
        """
        file_start_time = time.time()
        context = self.context_builder.build_prompt_context(target_file_name_or_path)
        target_file_name = context["file_name"]
        sub_folder = context.get("sub_folder", "")
        test_file_path = self.get_test_file_path(target_file_name, sub_folder)
        stem = Path(target_file_name).stem

        self.logger.info(f"\n{'='*76}")
        self.logger.info(f"  TARGET FILE TO TEST: {target_file_name} (Folder: '{sub_folder}')")
        self.logger.info(f"  Output Test File:    {test_file_path}")
        self.logger.info(f"  Target Coverage:     >={self.target_coverage_pct:.1f}%")
        self.logger.info(f"{'='*76}\n")

        critic_feedback: Optional[Dict[str, Any]] = None
        previous_code: Optional[str] = None
        best_coverage = 0.0
        initial_coverage = 0.0

        # Check and auto-migrate legacy names (*Tests.cs or flat root files)
        test_dir = self.test_csproj.parent
        legacy_nested_tests = test_file_path.parent / f"{stem}Tests.cs"
        legacy_flat_test = test_dir / f"{stem}Test.cs"
        legacy_flat_tests = test_dir / f"{stem}Tests.cs"

        if not test_file_path.exists():
            if legacy_nested_tests.exists():
                legacy_nested_tests.replace(test_file_path)
                self.logger.info(f"[MIGRATED] Renamed legacy test file '{legacy_nested_tests.name}' -> '{test_file_path.name}'")
            elif legacy_flat_test.exists() and legacy_flat_test != test_file_path:
                test_file_path.parent.mkdir(parents=True, exist_ok=True)
                legacy_flat_test.replace(test_file_path)
                self.logger.info(f"[MIGRATED] Moved '{legacy_flat_test.name}' into nested directory '{sub_folder}/'")
            elif legacy_flat_tests.exists() and legacy_flat_tests != test_file_path:
                test_file_path.parent.mkdir(parents=True, exist_ok=True)
                legacy_flat_tests.replace(test_file_path)
                self.logger.info(f"[MIGRATED] Moved and renamed '{legacy_flat_tests.name}' -> '{test_file_path.name}' in '{sub_folder}/'")

        original_existing_code: Optional[str] = (
            test_file_path.read_text(encoding="utf-8", errors="ignore")
            if test_file_path.exists()
            else None
        )

        file_memory = FileIterationMemory(target_file_name)

        best_passing_code: Optional[str] = None
        best_passing_coverage: float = 0.0
        best_passing_iteration: int = 0

        best_compiling_code: Optional[str] = None
        best_compiling_coverage: float = 0.0
        best_compiling_iteration: int = 0

        # ── Step 1: Check existing test file ──
        if test_file_path.exists() and test_file_path.stat().st_size > 50:
            self.logger.info(f"[FOUND] Existing test file found: {test_file_path}")
            self.logger.info("[INFO] Critic Agent evaluating existing tests baseline...")
            eval_result = self.critic.run_tests_and_evaluate(target_file_name)
            cov = eval_result.get("coverage_pct", 0.0)
            initial_coverage = cov
            file_memory.record_iteration(0, original_existing_code or "", eval_result)
            if eval_result.get("status") != "COMPILE_ERROR":
                best_compiling_code = original_existing_code
                best_compiling_coverage = cov
                best_compiling_iteration = 0
            if eval_result.get("passed", False):
                best_passing_code = original_existing_code
                best_passing_coverage = cov
                best_passing_iteration = 0

            # Log precise baseline evaluation
            log_critic_evaluation(target_file_name, 0, eval_result, self.target_coverage_pct)

            if eval_result.get("target_met"):
                file_memory.clear()
                self.logger.info(f"[ALREADY PASSED] Existing test file meets coverage target: {cov:.1f}% >= {self.target_coverage_pct}%\n")
                log_file_final_summary(
                    target_file=target_file_name,
                    test_file_path=str(test_file_path),
                    status="SUCCESS",
                    initial_cov=initial_coverage,
                    final_cov=cov,
                    iterations_used=0,
                    duration_sec=time.time() - file_start_time,
                    target_coverage_pct=self.target_coverage_pct,
                )
                return {
                    "file": target_file_name,
                    "test_file": str(test_file_path),
                    "status": "SUCCESS",
                    "coverage_pct": cov,
                    "iterations": 0,
                }
            else:
                self.logger.info(f"[REFINE NEEDED] Existing tests have status: {eval_result['status']} ({cov:.1f}% coverage).")
                self.logger.info("   Feeding existing tests & missing coverage gaps to Author Agent for targeted completion...\n")
                critic_feedback = eval_result
                previous_code = test_file_path.read_text(encoding="utf-8", errors="ignore")

        # ── Step 2: Generation / Refinement Loop ──
        for iteration in range(1, self.max_retries + 1):
            action_desc = "refining existing test cases" if previous_code else "writing test cases from scratch"
            self.logger.info(f"\n>>> [ITERATION {iteration}/{self.max_retries}] Author Agent ({self.author.provider.title()}: {self.author.model_name}) {action_desc}...")
            start_time = time.time()
            try:
                test_code = self.author.generate_tests(
                    context=context,
                    critic_feedback=critic_feedback,
                    previous_code=previous_code,
                    iteration=iteration,
                    memory_prompt=file_memory.format_memory_prompt(),
                )
            except AllKeysRateLimitedError as e:
                self.logger.error(f"   [RATE LIMITED] All API keys have exceeded rate limits: {e}")
                log_file_final_summary(
                    target_file=target_file_name,
                    test_file_path=str(test_file_path) if test_file_path.exists() else "",
                    status="RATE_LIMITED",
                    initial_cov=initial_coverage,
                    final_cov=best_coverage,
                    iterations_used=iteration - 1,
                    duration_sec=time.time() - file_start_time,
                    target_coverage_pct=self.target_coverage_pct,
                )
                return {
                    "file": target_file_name,
                    "test_file": str(test_file_path) if test_file_path.exists() else "",
                    "status": "RATE_LIMITED",
                    "coverage_pct": best_coverage,
                    "iterations": iteration - 1,
                    "error": str(e),
                }
            elapsed = time.time() - start_time

            # Validate generated code: ensure it contains C# class/namespace definition and not conversational refusal
            if not test_code or ("class " not in test_code and "namespace " not in test_code):
                self.logger.warning(f"   [INVALID CODE] Author Agent response on iteration {iteration} did not contain a valid C# class definition.")
                if previous_code and ("class " in previous_code or "namespace " in previous_code):
                    self.logger.warning("   [RECOVERY] Preserving previous test code instead of overwriting with invalid output.")
                    test_code = previous_code

            # Write generated test code to disk
            test_file_path.write_text(test_code, encoding="utf-8")

            # Critic Agent executes and measures coverage
            self.logger.info(f">>> [ITERATION {iteration}/{self.max_retries}] Critic Agent executing tests & measuring coverage...")
            eval_result = self.critic.run_tests_and_evaluate(target_file_name)
            status = eval_result["status"]
            coverage = eval_result.get("coverage_pct", 0.0)

            # Log precise evaluation card (pass/fail, coverage, uncovered lines, errors)
            log_critic_evaluation(target_file_name, iteration, eval_result, self.target_coverage_pct)

            # Record this attempt in file iteration memory cache
            file_memory.record_iteration(iteration, test_code, eval_result)

            passed_all = eval_result.get("passed", False)

            # Track best compiling code & best 100% passing code across iterations
            if status != "COMPILE_ERROR":
                if coverage >= best_compiling_coverage or best_compiling_code is None:
                    best_compiling_coverage = coverage
                    best_compiling_code = test_code
                    best_compiling_iteration = iteration

                if passed_all:
                    if coverage >= best_passing_coverage or best_passing_code is None:
                        best_passing_coverage = coverage
                        best_passing_code = test_code
                        best_passing_iteration = iteration

            if eval_result["target_met"]:
                file_memory.clear()
                self.logger.info(f"\n[TARGET MET] Coverage: {coverage:.1f}% >= {self.target_coverage_pct}% achieved on iteration {iteration}!")
                log_file_final_summary(
                    target_file=target_file_name,
                    test_file_path=str(test_file_path),
                    status="SUCCESS",
                    initial_cov=initial_coverage,
                    final_cov=coverage,
                    iterations_used=iteration,
                    duration_sec=time.time() - file_start_time,
                    target_coverage_pct=self.target_coverage_pct,
                )
                return {
                    "file": target_file_name,
                    "test_file": str(test_file_path),
                    "status": "SUCCESS",
                    "coverage_pct": coverage,
                    "iterations": iteration,
                }
            else:
                best_cov_display = best_passing_coverage if best_passing_code else best_compiling_coverage
                self.logger.info(f"[RETRY SCHEDULED] Status: {status} | Coverage: {coverage:.1f}% (Best passing: {best_passing_coverage:.1f}%)")
                critic_feedback = eval_result
                # Retrieve base code from memory cache (auto-rolls back if iteration caused regression)
                previous_code = file_memory.get_base_code_for_next_iteration(test_code)

        # If loop finishes without meeting target threshold
        best_code_to_restore: Optional[str] = None
        best_cov_final: float = 0.0
        best_iter_final: int = 0
        is_fully_passing: bool = False

        if best_passing_code:
            best_code_to_restore = best_passing_code
            best_cov_final = best_passing_coverage
            best_iter_final = best_passing_iteration
            is_fully_passing = True
        elif best_compiling_code:
            best_code_to_restore = best_compiling_code
            best_cov_final = best_compiling_coverage
            best_iter_final = best_compiling_iteration
            is_fully_passing = False

        self.logger.info(f"\n[MAX RETRIES REACHED] Finished {self.max_retries} iterations. Best achieved passing coverage: {best_passing_coverage:.1f}%")

        # Never delete test files! Restore the best passing (or compiling) version.
        final_status = "PARTIAL_OR_FAILED"
        if best_code_to_restore and test_file_path.exists():
            current_on_disk = test_file_path.read_text(encoding="utf-8", errors="ignore")
            if current_on_disk != best_code_to_restore:
                test_file_path.write_text(best_code_to_restore, encoding="utf-8")
                pass_label = "all tests passed" if is_fully_passing else "compiling with test failures"
                self.logger.info(
                    f"   [RESTORE TO BEST] Restored best test code ({best_cov_final:.1f}% coverage, {pass_label}, from iteration {best_iter_final}) to '{test_file_path.name}'."
                )
            if is_fully_passing and best_cov_final >= self.target_coverage_pct:
                final_status = "SUCCESS"
            elif is_fully_passing:
                final_status = "PARTIAL_SUCCESS"
            else:
                final_status = "TESTS_FAILED"
        elif not best_code_to_restore:
            if original_existing_code:
                test_file_path.write_text(original_existing_code, encoding="utf-8")
                self.logger.warning(f"   [REVERT TO ORIGINAL] Reverted '{test_file_path.name}' to original pre-run code.")
            else:
                self.logger.warning(
                    f"   [NOTIFICATION] Test file '{test_file_path.name}' was retained on disk with compilation issues for user review. Per policy, test files are NEVER deleted."
                )

        log_file_final_summary(
            target_file=target_file_name,
            test_file_path=str(test_file_path),
            status=final_status,
            initial_cov=initial_coverage,
            final_cov=best_cov_final,
            iterations_used=self.max_retries,
            duration_sec=time.time() - file_start_time,
            target_coverage_pct=self.target_coverage_pct,
        )

        # Clear memory cache for this file once loop completes
        file_memory.clear()

        return {
            "file": target_file_name,
            "test_file": str(test_file_path) if test_file_path.exists() else "",
            "status": final_status,
            "coverage_pct": best_cov_final,
            "iterations": self.max_retries,
        }

