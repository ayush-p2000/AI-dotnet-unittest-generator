from pathlib import Path
from typing import Any, Dict, List, Optional

from sonar.matcher import IssueMatcher, MethodMatch
from sonar.models import SonarIssue
from sonar.refactor_agent import RefactorAgent
from sonar.verifier_agent import VerifierAgent, VerificationResult


class SonarRefactorLoop:
    """
    Coordinates the iterative dual-agent loop:
      RefactorAgent (Generative) <---> VerifierAgent (Build/Test)
    """

    def __init__(
        self,
        matcher: IssueMatcher,
        verifier_agent: VerifierAgent,
        refactor_agent: Optional[RefactorAgent] = None,
        max_retries: int = 3,
    ):
        self.matcher = matcher
        self.verifier = verifier_agent
        self.refactorer = refactor_agent or RefactorAgent()
        self.max_retries = max_retries

    def resolve_issue(
        self,
        issue: SonarIssue,
        dry_run: bool = False,
        run_tests: bool = True,
    ) -> Dict[str, Any]:
        """
        Processes a single Cognitive Complexity issue.
        """
        # 1. Locate physical source file
        file_path = self.matcher.find_file(issue.file_path)
        if not file_path:
            return {
                "status": "FILE_NOT_FOUND",
                "message": f"Could not find physical file matching '{issue.file_path}'",
                "iterations": 0,
            }

        # 2. Locate target method
        method_match = self.matcher.locate_method(file_path, issue.line)
        if not method_match:
            return {
                "status": "METHOD_NOT_FOUND",
                "message": f"Could not locate method in '{file_path}' around line {issue.line}",
                "iterations": 0,
            }

        stats = issue.complexity_stats
        print(f"\n[TARGET] {file_path.name} -> {method_match.method_name}()")
        print(f"         Complexity: {stats['current']} (Target: <= {stats['allowed']})")
        print(f"         Method Lines: {method_match.start_line} - {method_match.end_line}")

        if dry_run:
            return {
                "status": "DRY_RUN",
                "file": str(file_path),
                "method": method_match.method_name,
                "lines": f"{method_match.start_line}-{method_match.end_line}",
                "complexity": stats,
                "iterations": 0,
            }

        # 3. Create safety backup
        self.verifier.backup_file(file_path)
        previous_errors: List[str] = []

        try:
            for iteration in range(1, self.max_retries + 1):
                print(f"  [Attempt {iteration}/{self.max_retries}] Invoking Generative Refactor Agent...")
                
                # Generative step
                refactored_code = self.refactorer.refactor_method(
                    issue=issue,
                    method_name=method_match.method_name,
                    original_method_code=method_match.method_code,
                    class_context=method_match.class_context,
                    previous_errors=previous_errors,
                    iteration=iteration,
                )

                # Stage refactored code
                replaced = self.matcher.replace_method(file_path, method_match, refactored_code)
                if not replaced:
                    self.verifier.rollback(file_path)
                    return {
                        "status": "REPLACE_FAILED",
                        "message": "Failed to replace method in file.",
                        "iterations": iteration,
                    }

                print("  [Attempt] Running Verifier Agent (dotnet build & test)...")
                verification: VerificationResult = self.verifier.verify(run_tests=run_tests)

                if verification.passed:
                    print(f"  [SUCCESS] Refactoring verified! Build passed & test regression clean.")
                    self.verifier.clean_backup(file_path)
                    return {
                        "status": "VERIFIED",
                        "file": str(file_path),
                        "method": method_match.method_name,
                        "iterations": iteration,
                        "refactored_code": refactored_code,
                    }
                else:
                    print(f"  [RETRY] Verification failed: {verification.status} with {len(verification.errors)} error(s).")
                    previous_errors = verification.errors

            # If all retries fail, roll back
            print("  [FAILED] Max retries exhausted. Rolling back to original code.")
            self.verifier.rollback(file_path)
            return {
                "status": "EXHAUSTED",
                "file": str(file_path),
                "method": method_match.method_name,
                "iterations": self.max_retries,
                "errors": previous_errors,
            }

        except Exception as e:
            self.verifier.rollback(file_path)
            return {
                "status": "ERROR",
                "file": str(file_path),
                "message": str(e),
                "iterations": 0,
            }
