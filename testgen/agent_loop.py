import time
from pathlib import Path
from typing import Any, Dict, Optional

from testgen.author import AuthorAgent, AllKeysRateLimitedError
from testgen.context import ContextBuilder
from testgen.critic import CriticAgent
from testgen.logger import get_logger


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
        context = self.context_builder.build_prompt_context(target_file_name_or_path)
        target_file_name = context["file_name"]
        sub_folder = context.get("sub_folder", "")
        test_file_path = self.get_test_file_path(target_file_name, sub_folder)
        stem = Path(target_file_name).stem

        self.logger.info(f"\n{'='*70}")
        self.logger.info(f"[INFO] Evaluating Target File: {target_file_name} (Folder: '{sub_folder}')")
        self.logger.info(f"[INFO] Output Test File: {test_file_path}")
        self.logger.info(f"{'='*70}\n")

        critic_feedback: Optional[Dict[str, Any]] = None
        previous_code: Optional[str] = None
        best_coverage = 0.0

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

        # ── Step 1: Check existing test file ──
        if test_file_path.exists() and test_file_path.stat().st_size > 50:
            self.logger.info(f"[FOUND] Existing test file found: {test_file_path}")
            self.logger.info("[INFO] Critic Agent evaluating existing tests...")
            eval_result = self.critic.run_tests_and_evaluate(target_file_name)
            cov = eval_result.get("coverage_pct", 0.0)
            best_coverage = cov

            if eval_result.get("target_met"):
                self.logger.info(f"[ALREADY PASSED] Existing test file meets coverage target: {cov:.1f}% >= {self.target_coverage_pct}%\n")
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
            self.logger.info(f"[Iteration {iteration}/{self.max_retries}] Author Agent ({self.author.provider.title()}: {self.author.model_name}) {action_desc}...")
            start_time = time.time()
            test_code = self.author.generate_tests(
                context=context,
                critic_feedback=critic_feedback,
                previous_code=previous_code,
            )
            elapsed = time.time() - start_time
            self.logger.info(f"   Test code generated in {elapsed:.1f}s ({len(test_code.splitlines())} lines).")

            # Write generated test code to disk
            test_file_path.write_text(test_code, encoding="utf-8")

            # Critic Agent executes and measures coverage
            self.logger.info(f"[Iteration {iteration}/{self.max_retries}] Critic Agent executing tests & measuring coverage...")
            eval_result = self.critic.run_tests_and_evaluate(target_file_name)
            status = eval_result["status"]
            coverage = eval_result.get("coverage_pct", 0.0)

            if coverage > best_coverage:
                best_coverage = coverage

            if eval_result["target_met"]:
                self.logger.info(f"\n[SUCCESS] All tests passed! Coverage: {coverage:.1f}% (Target: {self.target_coverage_pct}%)")
                return {
                    "file": target_file_name,
                    "test_file": str(test_file_path),
                    "status": "SUCCESS",
                    "coverage_pct": coverage,
                    "iterations": iteration,
                }
            else:
                self.logger.info(f"[RETRY NEEDED] Status: {status} | Current Coverage: {coverage:.1f}%")
                self.logger.info(f"   Feedback for Author Agent: {eval_result['feedback_message']}\n")
                critic_feedback = eval_result
                previous_code = test_code

        # If loop finishes without meeting the exact >= 90% threshold
        self.logger.info(f"\n[MAX RETRIES] Reached max retries ({self.max_retries}). Best achieved coverage: {best_coverage:.1f}%")

        # Cleanup uncompilable test code so it doesn't break subsequent project builds
        if best_coverage == 0.0 or (critic_feedback and critic_feedback.get("status") == "COMPILE_ERROR"):
            if original_existing_code:
                test_file_path.write_text(original_existing_code, encoding="utf-8")
                self.logger.warning(f"   [REVERT] Reverted '{test_file_path.name}' to previous version because generated tests failed to compile.")
            elif test_file_path.exists():
                try:
                    test_file_path.unlink()
                    self.logger.warning(f"   [CLEANUP] Deleted uncompilable test file '{test_file_path.name}' to prevent project build corruption.")
                except Exception as e:
                    self.logger.warning(f"   [CLEANUP] Could not remove test file: {e}")

        return {
            "file": target_file_name,
            "test_file": str(test_file_path) if test_file_path.exists() else "",
            "status": "PARTIAL_OR_FAILED",
            "coverage_pct": best_coverage,
            "iterations": self.max_retries,
        }
