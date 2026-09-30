"""
Centralized Logging and Audit System for TestGen
================================================
Dual-channel logging system:
1. Console: High-signal, formatted cards, progress bars, and execution breakdowns.
2. File Handler: Full untruncated prompts, responses, AST symbols, test traces,
   and coverage metrics saved in <User Documents Folder>/Logs/testgen_YYYYMMDD_HHMMSS.log
3. Audit Trail: Granular per-iteration prompt text, generated test C# code, and
   JSON evaluation artifacts saved in <User Documents Folder>/Logs/audit/<session>/
"""
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Global logger instances
_logger: Optional[logging.Logger] = None
_log_file_path: Optional[Path] = None
_audit_dir: Optional[Path] = None
_current_session_name: str = "testgen"


def get_logs_directory() -> Path:
    """Returns the user's Documents/Logs directory."""
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        docs_dir = Path(user_profile) / "Documents"
    else:
        docs_dir = Path.home() / "Documents"

    logs_dir = docs_dir / "Logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_audit_directory(session_name: Optional[str] = None) -> Path:
    """Returns the directory for storing detailed audit artifacts (prompts, raw code, eval JSONs)."""
    global _audit_dir, _current_session_name
    sess = session_name or _current_session_name
    sanitized = sess.replace(" ", "_").replace("/", "_").replace("\\", "_")
    audit_base = get_logs_directory() / "audit" / sanitized
    audit_base.mkdir(parents=True, exist_ok=True)
    _audit_dir = audit_base
    return audit_base


def setup_logger(
    session_name: str = "testgen",
    log_level: int = logging.INFO,
    custom_log_dir: Optional[Path] = None,
) -> logging.Logger:
    """Initializes and configures the centralized logger with dual handlers."""
    global _logger, _log_file_path, _current_session_name

    _current_session_name = session_name

    logs_dir = custom_log_dir or get_logs_directory()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sanitized_session = session_name.replace(" ", "_").replace("/", "_").replace("\\", "_")
    log_file_name = f"{sanitized_session}_{timestamp}.log"
    _log_file_path = logs_dir / log_file_name

    logger = logging.getLogger("testgen")
    logger.setLevel(log_level)
    logger.handlers.clear()
    logger.propagate = False

    # Detailed formatter for file: include exact microsecond timestamps
    file_formatter = logging.Formatter(
        "[%(asctime)s.%(msecs)03d] [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Clean formatter for console
    console_formatter = logging.Formatter("%(message)s")

    # File Handler
    try:
        file_handler = logging.FileHandler(
            str(_log_file_path),
            mode="a",
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"[WARNING] Could not create file log handler at '{_log_file_path}': {e}", file=sys.stderr)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    _logger = logger

    # Initialize audit directory
    get_audit_directory(sanitized_session)

    logger.info("=" * 76)
    logger.info("  AI C# TEST GENERATOR - PRECISION EXECUTION LOG SESSION")
    logger.info(f"  Session:    {session_name}")
    logger.info(f"  Log File:   {_log_file_path}")
    logger.info(f"  Audit Dir:  {_audit_dir}")
    logger.info(f"  Started:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 76)

    return logger


def get_logger() -> logging.Logger:
    """Returns the configured logger, or initializes default."""
    global _logger
    if _logger is None:
        return setup_logger()
    return _logger


def get_current_log_file() -> Optional[Path]:
    """Returns the active log file path."""
    return _log_file_path


# ─────────────────────────────────────────────────────────────────────────────
# Helper Formatters
# ─────────────────────────────────────────────────────────────────────────────

def format_line_ranges(lines: List[int]) -> str:
    """Converts a list of line numbers [1, 2, 3, 5, 8, 9] into '1-3, 5, 8-9'."""
    if not lines:
        return "None"
    sorted_lines = sorted(set(lines))
    ranges = []
    start = sorted_lines[0]
    prev = sorted_lines[0]

    for line in sorted_lines[1:]:
        if line == prev + 1:
            prev = line
        else:
            if start == prev:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}-{prev}")
            start = line
            prev = line

    if start == prev:
        ranges.append(str(start))
    else:
        ranges.append(f"{start}-{prev}")

    return ", ".join(ranges)


def format_progress_bar(pct: float, width: int = 24) -> str:
    """Generates a visual ASCII progress bar: [████████████████░░░░░░░░] 66.7%"""
    clamped = max(0.0, min(100.0, pct))
    filled = int(round(width * (clamped / 100.0)))
    empty = width - filled
    bar = "█" * filled + "░" * empty
    return f"[{bar}] {clamped:.1f}%"


def log_banner(title: str, subtitle: Optional[str] = None, width: int = 76) -> None:
    """Prints a styled section banner."""
    logger = get_logger()
    border = "=" * width
    logger.info(f"\n{border}")
    logger.info(f"  {title.upper()}")
    if subtitle:
        logger.info(f"  {subtitle}")
    logger.info(f"{border}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Precise Logging: Prompt, Response, Coverage, Failures
# ─────────────────────────────────────────────────────────────────────────────

def log_prompt_details(
    target_file: str,
    iteration: int,
    model_name: str,
    provider: str,
    system_prompt: str,
    user_prompt: str,
    est_tokens: int,
    session_name: Optional[str] = None,
) -> None:
    """
    Logs precise AI prompt information to console, web UI, active log file,
    and dedicated audit file.
    """
    logger = get_logger()
    stem = Path(target_file).stem
    audit_dir = get_audit_directory(session_name)
    prompt_file = audit_dir / f"{stem}_iter_{iteration}_prompt.txt"

    # Save complete untruncated prompt to audit file
    try:
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(f"MODEL: {provider.upper()} ({model_name})\n")
            f.write(f"TIMESTAMP: {datetime.now().isoformat()}\n")
            f.write(f"TARGET_FILE: {target_file}\n")
            f.write(f"ITERATION: {iteration}\n")
            f.write(f"EST_TOKENS: {est_tokens}\n\n")
            f.write("=== SYSTEM PROMPT ===\n")
            f.write(system_prompt + "\n\n")
            f.write("=== USER PROMPT ===\n")
            f.write(user_prompt + "\n")
    except Exception as e:
        logger.debug(f"Could not write prompt audit file: {e}")

    # 1. Console & UI Summary Card
    logger.info("-" * 76)
    logger.info(f"[PROMPT SENT] Target: {target_file} | Iteration {iteration}")
    logger.info(f"  Provider:         {provider.upper()} ({model_name})")
    logger.info(f"  System Prompt:    {len(system_prompt):,} characters ({len(system_prompt.splitlines())} lines)")
    logger.info(f"  User Prompt:      {len(user_prompt):,} characters ({len(user_prompt.splitlines())} lines)")
    logger.info(f"  Estimated Tokens: ~{est_tokens:,} tokens")
    logger.info(f"  Audit File:       {prompt_file}")
    logger.info("-" * 76)

    # 2. Detailed Prompt Content Section (Full Raw Inputs)
    logger.info("┌── [AUTHOR INPUT: FULL RAW SYSTEM PROMPT] " + "─" * 38)
    for line in system_prompt.splitlines():
        logger.info(f"│ {line}")
    logger.info("└── [END RAW SYSTEM PROMPT] " + "─" * 52)

    logger.info("┌── [AUTHOR INPUT: FULL RAW USER PROMPT] " + "─" * 40)
    for line in user_prompt.splitlines():
        logger.info(f"│ {line}")
    logger.info("└── [END RAW USER PROMPT] " + "─" * 54)

    # 3. Write untruncated prompt block to session log file
    file_banner = (
        f"\n{'#'*80}\n"
        f"### PROMPT AUDIT DUMP: {target_file} | Iteration {iteration} | Model: {model_name}\n"
        f"{'#'*80}\n"
        f"--- SYSTEM PROMPT ---\n{system_prompt}\n\n"
        f"--- USER PROMPT ---\n{user_prompt}\n"
        f"{'#'*80}\n"
        f"### END PROMPT AUDIT DUMP\n"
        f"{'#'*80}\n"
    )
    if _log_file_path and _log_file_path.exists():
        try:
            with open(_log_file_path, "a", encoding="utf-8") as f:
                f.write(file_banner)
        except Exception:
            pass


def log_ai_result_details(
    target_file: str,
    iteration: int,
    model_name: str,
    provider: str,
    generated_code: str,
    elapsed_sec: float,
    raw_response: Optional[str] = None,
    usage: Optional[Dict[str, Any]] = None,
    session_name: Optional[str] = None,
) -> None:
    """
    Logs precise AI generation output details, line count, duration,
    and logs the generated C# test code / raw output directly to console,
    web UI, log file, and audit file.
    """
    logger = get_logger()
    stem = Path(target_file).stem
    audit_dir = get_audit_directory(session_name)
    code_file = audit_dir / f"{stem}_iter_{iteration}_tests.cs"

    # Save to dedicated audit file
    try:
        with open(code_file, "w", encoding="utf-8") as f:
            f.write(f"// Generated by TestGen ({provider}: {model_name}) in {elapsed_sec:.2f}s\n")
            f.write(f"// Target: {target_file} | Iteration: {iteration}\n\n")
            f.write(generated_code)
    except Exception as e:
        logger.debug(f"Could not write code audit file: {e}")

    lines = generated_code.splitlines()
    code_line_count = len(lines)

    # 1. Console & UI Summary Card
    logger.info("-" * 76)
    logger.info(f"[AI RESULT RECEIVED] Target: {target_file} | Iteration {iteration}")
    logger.info(f"  Model Used:       {provider.upper()}: {model_name}")
    logger.info(f"  Generation Time:  {elapsed_sec:.2f} seconds")
    logger.info(f"  Code Output:      {code_line_count:,} lines ({len(generated_code):,} chars)")
    if usage:
        prompt_tok = usage.get("prompt_tokens", "N/A")
        comp_tok = usage.get("completion_tokens", "N/A")
        total_tok = usage.get("total_tokens", "N/A")
        logger.info(f"  Token Usage:      Prompt: {prompt_tok} | Completion: {comp_tok} | Total: {total_tok}")
    logger.info(f"  Audit Test File:  {code_file}")
    logger.info("-" * 76)

    # 2. Extract and list detected test methods
    test_methods = re.findall(
        r"\[(?:Fact|Theory)\](?:\s*\[[^\]]+\])*\s*public\s+(?:async\s+Task|void)\s+([A-Za-z0-9_]+)",
        generated_code,
    )
    if test_methods:
        logger.info(f"  [DISCOVERED TESTS] Found {len(test_methods)} unit test method(s):")
        for idx, tm in enumerate(test_methods, 1):
            logger.info(f"    {idx:2d}. {tm}")
    elif code_line_count > 0:
        logger.warning("  ⚠ [WARNING] 0 unit test methods ([Fact]/[Theory]) detected in AI output!")

    # 3. Log Full Raw Model Output (What We Got From AI)
    display_raw = raw_response if raw_response is not None else generated_code
    logger.info(f"┌── [AUTHOR OUTPUT: FULL RAW MODEL RESPONSE ({len(display_raw.splitlines())} lines)] " + "─" * 15)
    for line in display_raw.splitlines():
        logger.info(f"│ {line}")
    logger.info("└── [END RAW MODEL RESPONSE] " + "─" * 50)

    # 4. If extracted code differs from raw response, log cleaned C# test code
    if generated_code.strip() != display_raw.strip():
        logger.info(f"┌── [AUTHOR OUTPUT: EXTRACTED C# TEST CODE ({code_line_count} lines)] " + "─" * 18)
        for idx, line in enumerate(lines, 1):
            logger.info(f"│ {idx:3d} | {line}")
        logger.info(f"└── [END EXTRACTED CODE: {target_file} | Iteration {iteration}] " + "─" * 25)

    # 5. Write untruncated code dump to session log file
    file_banner = (
        f"\n{'='*80}\n"
        f"=== GENERATED C# TEST CODE DUMP: {target_file} | Iteration {iteration}\n"
        f"=== Elapsed: {elapsed_sec:.2f}s | Lines: {code_line_count}\n"
        f"{'='*80}\n"
        f"{generated_code}\n"
        f"{'='*80}\n"
        f"=== END GENERATED C# TEST CODE DUMP\n"
        f"{'='*80}\n"
    )
    if _log_file_path and _log_file_path.exists():
        try:
            with open(_log_file_path, "a", encoding="utf-8") as f:
                f.write(file_banner)
        except Exception:
            pass


def log_critic_evaluation(
    target_file: str,
    iteration: int,
    eval_result: Dict[str, Any],
    target_coverage_pct: float = 90.0,
    session_name: Optional[str] = None,
) -> None:
    """
    Logs precise test execution and code coverage metrics:
    - Critic Input: Target file, test project, executed CLI command
    - Status (SUCCESS / LOW_COVERAGE / TEST_FAILURE / COMPILE_ERROR)
    - Test Counts: Total, Passed, Failed, Skipped
    - Coverage: Line coverage %, covered/total lines, visual bar, uncovered line ranges
    - Compile Errors: CS codes, file, line number
    - Test Failures: failed test name, assertion error message, stack trace
    - Full Raw Critic Output: complete stdout and stderr from dotnet test
    """
    logger = get_logger()
    stem = Path(target_file).stem

    status = eval_result.get("status", "UNKNOWN")
    passed = eval_result.get("passed", False)
    target_met = eval_result.get("target_met", False)
    cov_pct = eval_result.get("coverage_pct", 0.0)
    total_lines = eval_result.get("total_lines", 0)
    covered_lines = eval_result.get("covered_lines", 0)
    uncovered = eval_result.get("uncovered_lines", [])

    test_counts = eval_result.get("test_counts", {})
    total_tests = test_counts.get("total", 0)
    passed_tests = test_counts.get("passed", 0)
    failed_tests = test_counts.get("failed", 0)
    skipped_tests = test_counts.get("skipped", 0)
    duration_s = test_counts.get("duration_s", 0.0)

    compile_errors = eval_result.get("compile_errors", [])
    test_failures = eval_result.get("test_failures", [])
    command_str = eval_result.get("command")
    test_proj = eval_result.get("test_project")
    raw_output = eval_result.get("raw_output", "")

    # 1. Critic Input Section
    if command_str or test_proj:
        logger.info("┌── [CRITIC INPUT: TEST EXECUTION CONFIGURATION] " + "─" * 28)
        logger.info(f"│ Target File:  {target_file} | Iteration: {iteration}")
        if test_proj:
            logger.info(f"│ Test Project: {test_proj}")
        if command_str:
            logger.info(f"│ Command:      {command_str}")
        logger.info("└── [END CRITIC INPUT] " + "─" * 56)

    # 2. Evaluation Summary Card
    if target_met:
        status_tag = "✓ SUCCESS (Target Met)"
    elif status == "LOW_COVERAGE":
        status_tag = "⚠ LOW COVERAGE (All Tests Passed, Need More Tests)"
    elif status == "TEST_FAILURE":
        status_tag = "✗ TEST RUNTIME FAILURE"
    elif status == "COMPILE_ERROR":
        status_tag = "✗ COMPILATION ERROR"
    else:
        status_tag = f"• {status}"

    logger.info("┌" + "─" * 74 + "┐")
    logger.info(f"│ CRITIC EVALUATION | Iteration {iteration} | Target: {target_file:<33} │")
    logger.info("├" + "─" * 74 + "┤")
    logger.info(f"│ Outcome Status:     {status_tag:<52} │")

    # Tests Run
    if total_tests > 0:
        pass_rate = (passed_tests / total_tests) * 100.0 if total_tests else 0.0
        test_info = f"Total: {total_tests} | Passed: {passed_tests} | Failed: {failed_tests} | Skipped: {skipped_tests} ({pass_rate:.1f}% pass)"
        logger.info(f"│ Test Execution:     {test_info:<52} │")
        if duration_s > 0:
            logger.info(f"│ Execution Duration: {duration_s:.2f} seconds{' '*44} │")

    # Coverage
    if total_lines > 0:
        cov_info = f"{cov_pct:.1f}% [{covered_lines}/{total_lines} lines covered]"
        logger.info(f"│ Line Coverage:      {cov_info:<52} │")
        bar_str = format_progress_bar(cov_pct, width=22)
        logger.info(f"│ Coverage Progress:  {bar_str} (Target: >={target_coverage_pct:.1f}%){' '*14} │")
        uncovered_str = format_line_ranges(uncovered)
        if len(uncovered_str) > 50:
            uncovered_str = uncovered_str[:47] + "..."
        logger.info(f"│ Uncovered Lines:    {uncovered_str:<52} │")
    else:
        logger.info(f"│ Line Coverage:      {cov_pct:.1f}% (No line data){' '*34} │")

    logger.info("└" + "─" * 74 + "┘")

    # 3. Full Raw Critic Output (stdout & stderr)
    if raw_output:
        raw_lines = raw_output.splitlines()
        logger.info(f"┌── [CRITIC OUTPUT: FULL RAW EXECUTION OUTPUT ({len(raw_lines)} lines)] " + "─" * 16)
        for line in raw_lines:
            logger.info(f"│ {line}")
        logger.info("└── [END RAW CRITIC OUTPUT] " + "─" * 53)

    # 4. Detailed Failure / Error Logs
    if compile_errors:
        logger.warning(f"\n[CRITIC] Found {len(compile_errors)} C# Compilation Error(s):")
        for idx, err in enumerate(compile_errors, 1):
            logger.warning(f"  {idx}. {err}")

    if test_failures:
        logger.warning(f"\n[CRITIC] Found {len(test_failures)} Failing Test(s):")
        for idx, failure_text in enumerate(test_failures, 1):
            logger.warning(f"\n  --- [Failure #{idx}] ---")
            for line in failure_text.splitlines():
                logger.warning(f"    {line}")

    # 3. Dedicated Audit File: audit/<session>/<file>_iter_<N>_eval.json
    try:
        audit_dir = get_audit_directory(session_name)
        eval_file = audit_dir / f"{stem}_iter_{iteration}_eval.json"
        audit_payload = {
            "target_file": target_file,
            "iteration": iteration,
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "passed": passed,
            "target_met": target_met,
            "coverage_pct": cov_pct,
            "target_coverage_pct": target_coverage_pct,
            "total_lines": total_lines,
            "covered_lines": covered_lines,
            "uncovered_lines": uncovered,
            "test_counts": test_counts,
            "compile_errors_count": len(compile_errors),
            "compile_errors": compile_errors,
            "test_failures_count": len(test_failures),
            "test_failures": test_failures,
        }
        with open(eval_file, "w", encoding="utf-8") as f:
            json.dump(audit_payload, f, indent=2)
    except Exception as e:
        logger.debug(f"Could not write eval audit JSON: {e}")


def log_file_final_summary(
    target_file: str,
    test_file_path: str,
    status: str,
    initial_cov: float,
    final_cov: float,
    iterations_used: int,
    duration_sec: float,
    target_coverage_pct: float = 90.0,
) -> None:
    """Prints a beautiful final summary card at the completion of a file test generation."""
    logger = get_logger()
    delta_cov = final_cov - initial_cov
    delta_str = f"+{delta_cov:.1f}%" if delta_cov >= 0 else f"{delta_cov:.1f}%"

    status_icon = "✓" if status == "SUCCESS" else "✗"
    logger.info("\n" + "=" * 76)
    logger.info(f"  FILE TEST GENERATION SUMMARY: {target_file}")
    logger.info("=" * 76)
    logger.info(f"  Final Status:        {status_icon} {status}")
    logger.info(f"  Target File:         {target_file}")
    logger.info(f"  Output Test File:    {test_file_path}")
    logger.info(f"  Initial Coverage:    {initial_cov:.1f}%")
    logger.info(f"  Final Coverage:      {final_cov:.1f}% ({delta_str} change)")
    logger.info(f"  Target Coverage:     >={target_coverage_pct:.1f}%")
    logger.info(f"  Iterations Used:     {iterations_used}")
    logger.info(f"  Total Duration:      {duration_sec:.2f} seconds")
    logger.info("=" * 76 + "\n")
