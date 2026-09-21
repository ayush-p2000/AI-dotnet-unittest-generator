"""
Full Solution Batch Orchestrator
=================================
Processes ALL testable source files in a .NET project through the 2-agent
(Author + Critic) loop.  Skips files that have no testable logic (enums,
plain DTOs/models, EF migrations, DbContext, Program.cs, etc.).

Usage:
    python batch_generate.py
    python batch_generate.py --manifest scan_output.json --project TicDrive
    python batch_generate.py --concurrency 2 --model gemini-3.6-flash
    python batch_generate.py --only-file BookingsService.cs   # resume a single file
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

try:
    from testgen.agent_loop import TestGenLoop
    from testgen.author import AllKeysRateLimitedError, AuthorAgent
    from testgen.context import ContextBuilder
    from testgen.logger import get_current_log_file, get_logger, setup_logger
    from testgen.scaffold import scaffold_test_project
    from testgen.state import StateTracker
except KeyboardInterrupt:
    print("\n\n[STOPPED] Execution cancelled by user (Ctrl+C). Exiting cleanly.")
    sys.exit(130)

# ──────────────────────────────────────────────────────────────────────
# Skip-list heuristics -- files that contain no meaningful testable logic
# ──────────────────────────────────────────────────────────────────────
SKIP_FILENAME_PATTERNS = {
    "Program.cs",
    "Startup.cs",
    "GlobalUsings.cs",
    "AssemblyInfo.cs",
}

SKIP_FILENAME_SUFFIXES = (
    "DbContext.cs",
    "DbContextModelSnapshot.cs",
    ".Designer.cs",
    ".g.cs",
    ".g.i.cs",
)

SKIP_FOLDER_SEGMENTS = {"Migrations", "migrations"}


def should_skip_file(file_info: Dict[str, Any]) -> tuple[bool, str]:
    """Return (should_skip, reason) for a file entry from the manifest."""
    name = file_info["file_name"]
    path = file_info.get("path", "")

    # 1. Explicit filename skip
    if name in SKIP_FILENAME_PATTERNS:
        return True, "boilerplate/entry point"

    # 2. Suffix-based skip (migrations, designer, dbcontext snapshot)
    for suffix in SKIP_FILENAME_SUFFIXES:
        if name.endswith(suffix):
            return True, f"matches skip suffix *{suffix}"

    # 3. Folder-based skip (Migrations)
    for seg in SKIP_FOLDER_SEGMENTS:
        if seg in Path(path).parts:
            return True, f"inside {seg}/ folder"

    # 4. Inspect the types -- skip pure enums, pure DTOs, interfaces
    types = file_info.get("types", [])
    if not types:
        return True, "no types found"

    all_trivial = True
    for t in types:
        kind = t.get("kind", "")
        methods = t.get("methods", [])
        ctors = t.get("constructors", [])

        # Enums are pure data
        if "enum" in kind:
            continue

        # Interfaces have no implementation
        if kind == "interface":
            continue

        # If a type has methods (business logic), it's testable
        if methods:
            all_trivial = False
            break

        # If it has constructors with parameters (DI), likely a service
        if ctors and any(c.get("params", "").strip() for c in ctors):
            all_trivial = False
            break

    if all_trivial:
        return True, "no testable logic (enum/DTO/interface/model)"

    return False, ""


def find_project(manifest: dict, project_hint: str = None) -> dict:
    """Select the source project from the manifest."""
    logger = get_logger()
    projects = manifest.get("projects", [])
    if not projects:
        logger.error("[ERROR] No projects found in manifest.")
        sys.exit(1)

    if project_hint:
        matches = [p for p in projects if p["project_name"].lower() == project_hint.lower()]
        if not matches:
            available = ", ".join(p["project_name"] for p in projects)
            logger.error(f"[ERROR] Project '{project_hint}' not found. Available: {available}")
            sys.exit(1)
        return matches[0]

    if len(projects) == 1:
        return projects[0]

    logger.info("[INFO] Multiple projects found:")
    for i, p in enumerate(projects, 1):
        logger.info(f"  {i}. {p['project_name']}")
    logger.error("Re-run with --project <name>")
    sys.exit(1)


def process_single_file(
    loop: TestGenLoop, file_name: str
) -> Dict[str, Any]:
    """Wrapper that catches exceptions so one file can't crash the batch."""
    try:
        return loop.process_file(file_name)
    except AllKeysRateLimitedError:
        raise
    except Exception as exc:
        logger = get_logger()
        logger.error(f"\n[ERROR] Exception processing {file_name}: {exc}", exc_info=True)
        return {
            "file": file_name,
            "test_file": "",
            "status": "ERROR",
            "coverage_pct": 0.0,
            "iterations": 0,
            "error": str(exc),
        }


def print_report(results: List[Dict[str, Any]], skipped: List[Dict[str, str]], elapsed_s: float):
    """Print a formatted summary table and stats using ASCII-only output."""
    logger = get_logger()
    success = [r for r in results if r["status"] == "SUCCESS"]
    partial = [r for r in results if r["status"] == "PARTIAL_OR_FAILED"]
    errors  = [r for r in results if r["status"] == "ERROR"]

    width = 80
    logger.info("\n" + "=" * width)
    logger.info("  BATCH TEST GENERATION REPORT")
    logger.info("=" * width)

    # Summary stats
    total_processed = len(results)
    avg_coverage = sum(r.get("coverage_pct", 0) for r in results) / max(total_processed, 1)
    total_iterations = sum(r.get("iterations", 0) for r in results)

    logger.info(f"\n  Files scanned:     {total_processed + len(skipped)}")
    logger.info(f"  Files processed:   {total_processed}")
    logger.info(f"  Files skipped:     {len(skipped)}")
    logger.info(f"  Succeeded (>=90%): {len(success)}")
    logger.info(f"  Partial/Failed:    {len(partial)}")
    logger.info(f"  Errors:            {len(errors)}")
    logger.info(f"  Avg coverage:      {avg_coverage:.1f}%")
    logger.info(f"  Total iterations:  {total_iterations}")
    logger.info(f"  Total time:        {elapsed_s:.0f}s ({elapsed_s/60:.1f} min)")

    # Detailed results table
    logger.info(f"\n{'-' * width}")
    logger.info(f"  {'File':<40} {'Status':<18} {'Coverage':>9} {'Iters':>6}")
    logger.info(f"{'-' * width}")

    for r in sorted(results, key=lambda x: x["file"]):
        status_icon = {
            "SUCCESS": "[OK]",
            "PARTIAL_OR_FAILED": "[PARTIAL]",
            "ERROR": "[ERR]",
        }.get(r["status"], "[???]")
        cov = f"{r.get('coverage_pct', 0):.1f}%"
        iters = str(r.get("iterations", 0))
        logger.info(f"  {r['file']:<40} {status_icon:<18} {cov:>9} {iters:>6}")

    # Skipped files
    if skipped:
        logger.info(f"\n{'-' * width}")
        logger.info(f"  SKIPPED FILES ({len(skipped)}):")
        logger.info(f"{'-' * width}")
        for s in sorted(skipped, key=lambda x: x["file"]):
            logger.info(f"  {s['file']:<40} -> {s['reason']}")

    logger.info(f"\n{'=' * width}\n")


def save_report_json(results: List[Dict[str, Any]], skipped: List[Dict[str, str]], elapsed_s: float, out_path: Path):
    """Persist the batch report as JSON for downstream tooling."""
    import stat
    logger = get_logger()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed_s, 1),
        "log_file": str(get_current_log_file() or ""),
        "summary": {
            "total_processed": len(results),
            "total_skipped": len(skipped),
            "success": len([r for r in results if r["status"] == "SUCCESS"]),
            "partial_or_failed": len([r for r in results if r["status"] == "PARTIAL_OR_FAILED"]),
            "errors": len([r for r in results if r["status"] == "ERROR"]),
            "avg_coverage_pct": round(
                sum(r.get("coverage_pct", 0) for r in results) / max(len(results), 1), 1
            ),
        },
        "results": results,
        "skipped": skipped,
    }
    if out_path.exists():
        try:
            os.chmod(str(out_path.resolve()), stat.S_IWRITE)
        except Exception:
            pass
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    try:
        os.chmod(str(out_path.resolve()), stat.S_IREAD)
    except Exception:
        pass
    logger.info(f"[INFO] Report saved to {out_path}")
    logger.info(f"[LOG SAVED] Detailed execution log saved to {get_current_log_file()}")




def main():
    parser = argparse.ArgumentParser(
        description="Batch-process all testable C# files through the AI test generator"
    )
    parser.add_argument("--manifest", default="scan_output.json", help="Path to scan manifest JSON")
    parser.add_argument("--project", default=None, help="Project name (for multi-project solutions)")
    parser.add_argument("--all-projects", action="store_true", help="Process all projects in the solution sequentially")
    parser.add_argument("--provider", default="auto", choices=["auto", "ollama", "gemini", "qwen-cloud"], help="AI provider agent (default: auto)")
    parser.add_argument("--model", default=None, help="Model name (default: auto-detected based on provider)")
    parser.add_argument("--coverage", type=float, default=90.0, help="Target coverage %%")
    parser.add_argument("--retries", type=int, default=4, help="Max author/critic iterations per file")
    parser.add_argument("--concurrency", type=int, default=1, help="Parallel workers (use 1 for sequential)")
    parser.add_argument("--only-file", default=None, help="Process only this single file (for resume)")
    parser.add_argument("--resume", action="store_true", help="Skip files that already have passing test files with >= target coverage")
    parser.add_argument("--force", action="store_true", help="Ignore saved checkpoint and re-evaluate all files")
    parser.add_argument("--state-file", default=".testgen_state.json", help="Path to state/checkpoint JSON file")
    parser.add_argument("--report", default="batch_report.json", help="Output report JSON path")
    parser.add_argument("--no-skip", action="store_true", help="Don't skip any files (process everything)")
    args = parser.parse_args()

    # Initialize centralized logger to Documents/Logs/
    proj_tag = args.project or ("all_projects" if args.all_projects else "batch")
    logger = setup_logger(session_name=f"testgen_{proj_tag}")

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        logger.error(f"[ERROR] Manifest not found: {manifest_path}. Run scan_project.py first.")
        sys.exit(1)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    if args.all_projects:
        projects_to_run = manifest.get("projects", [])
    else:
        projects_to_run = [find_project(manifest, args.project)]

    root = Path(manifest["root"]).resolve()
    context_builder = ContextBuilder(manifest)
    state_tracker = StateTracker(state_file=args.state_file)
    shared_author = AuthorAgent(model_name=args.model, provider=args.provider)

    all_batch_results: List[Dict[str, Any]] = []
    all_batch_skipped: List[Dict[str, str]] = []
    start_total = time.time()
    rate_limit_interrupted = False
    keyboard_interrupted = False

    for project in projects_to_run:
        project_name = project["project_name"]
        test_csproj = root / "tests" / f"{project_name}.Tests" / f"{project_name}.Tests.csproj"

        if not test_csproj.exists():
            logger.info(f"[INFO] Test project not found at {test_csproj}. Automatically scaffolding test project...")
            test_csproj = Path(scaffold_test_project(root, project["csproj"], project_name)).resolve()

        loop = TestGenLoop(
            context_builder=context_builder,
            test_csproj=test_csproj,
            model_name=args.model,
            target_coverage_pct=args.coverage,
            max_retries=args.retries,
            author_agent=shared_author,
        )

        # ── Build file list ──
        all_files = project["files"]
        skipped: List[Dict[str, str]] = []
        to_process: List[str] = []

        for f in all_files:
            name = f["file_name"]

            # If --only-file is set, skip everything else
            if args.only_file and name != args.only_file:
                continue

            if not args.no_skip:
                skip, reason = should_skip_file(f)
                if skip:
                    skipped.append({"file": name, "reason": reason})
                    continue

            to_process.append(name)

        all_batch_skipped.extend(skipped)

        logger.info(f"\n{'=' * 70}")
        logger.info(f"  BATCH TEST GENERATOR")
        logger.info(f"  Project:    {project_name}")
        logger.info(f"  Model:      {args.model}")
        logger.info(f"  Target:     >= {args.coverage}% coverage")
        logger.info(f"  Files:      {len(to_process)} testable, {len(skipped)} skipped")
        logger.info(f"  Workers:    {args.concurrency}")
        logger.info(f"  Checkpoint: {args.state_file}")
        logger.info(f"{'=' * 70}\n")

        if not to_process:
            logger.info(f"[INFO] No files to process for {project_name}.")
            continue

        # ── Execute ──
        try:
            if args.concurrency <= 1:
                # Sequential mode (safest -- single dotnet test at a time)
                for i, file_name in enumerate(to_process, 1):
                    # Check saved checkpoint state first (Zero-cost instant resume!)
                    if not args.force and state_tracker.is_completed(project_name, file_name, min_coverage=args.coverage):
                        entry = state_tracker.get_file_entry(project_name, file_name)
                        logger.info(f"[{i}/{len(to_process)}] [CHECKPOINT-HIT] {file_name} already completed ({entry.get('coverage_pct', 0.0):.1f}% cov).")
                        all_batch_results.append({
                            "file": file_name,
                            "test_file": entry.get("test_file", ""),
                            "status": entry.get("status", "SUCCESS"),
                            "coverage_pct": entry.get("coverage_pct", 0.0),
                            "iterations": entry.get("iterations", 0),
                        })
                        continue

                    logger.info(f"\n[{i}/{len(to_process)}] Processing: {file_name}")
                    result = process_single_file(loop, file_name)
                    state_tracker.record_file_result(project_name, file_name, result)
                    all_batch_results.append(result)
            else:
                # Parallel mode with checkpoint skipping (Bug 3 fix)
                pending_files = []
                for i, file_name in enumerate(to_process, 1):
                    if not args.force and state_tracker.is_completed(project_name, file_name, min_coverage=args.coverage):
                        entry = state_tracker.get_file_entry(project_name, file_name)
                        logger.info(f"[{i}/{len(to_process)}] [CHECKPOINT-HIT] {file_name} already completed ({entry.get('coverage_pct', 0.0):.1f}% cov).")
                        all_batch_results.append({
                            "file": file_name,
                            "test_file": entry.get("test_file", ""),
                            "status": entry.get("status", "SUCCESS"),
                            "coverage_pct": entry.get("coverage_pct", 0.0),
                            "iterations": entry.get("iterations", 0),
                        })
                    else:
                        pending_files.append(file_name)

                if pending_files:
                    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                        future_to_file = {
                            pool.submit(process_single_file, loop, name): name
                            for name in pending_files
                        }
                        try:
                            for i, future in enumerate(as_completed(future_to_file), 1):
                                file_name = future_to_file[future]
                                try:
                                    result = future.result()
                                    state_tracker.record_file_result(project_name, file_name, result)
                                    all_batch_results.append(result)
                                    status = result.get("status", "???")
                                    cov = result.get("coverage_pct", 0)
                                    logger.info(f"[{i}/{len(pending_files)}] {file_name}: {status} ({cov:.1f}%)")
                                except AllKeysRateLimitedError:
                                    raise
                        except KeyboardInterrupt:
                            try:
                                pool.shutdown(wait=False, cancel_futures=True)
                            except Exception:
                                pass
                            raise
        except AllKeysRateLimitedError:
            rate_limit_interrupted = True
            logger.warning("\n" + "!" * 70)
            logger.warning(f" [RATE LIMIT EXHAUSTED] All {shared_author.provider.upper()} API keys have hit their daily quota limit.")
            logger.warning(f" Progress has been saved to '{args.state_file}'.")
            logger.warning(" Re-run this script anytime later to automatically resume from where it left off!")
            logger.warning("!" * 70 + "\n")
            break
        except KeyboardInterrupt:
            keyboard_interrupted = True
            logger.warning("\n" + "!" * 70)
            logger.warning(" [STOPPED BY USER] Batch execution cancelled via Ctrl+C (KeyboardInterrupt).")
            logger.warning(f" Progress up to the last completed file has been saved to '{args.state_file}'.")
            logger.warning(" Re-run this script anytime to automatically resume from where it left off!")
            logger.warning("!" * 70 + "\n")
            break

    elapsed = time.time() - start_total

    # ── Ensure State is Flushed ──
    try:
        state_tracker.save()
    except Exception:
        pass

    # ── Report ──
    print_report(all_batch_results, all_batch_skipped, elapsed)
    save_report_json(all_batch_results, all_batch_skipped, elapsed, Path(args.report))

    if rate_limit_interrupted:
        logger.info(f"[INFO] Batch run paused due to API quota. Resume later with: python batch_generate.py")
    elif keyboard_interrupted:
        logger.info(f"[INFO] Batch run halted by user. Resume anytime with: python batch_generate.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[STOPPED] Execution interrupted by user (Ctrl+C). Exiting cleanly.")
        sys.exit(130)
