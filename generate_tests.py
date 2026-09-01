"""
CLI entry point for the AI-powered C# test generator.
Works on any .NET 8 C# project -- no project-specific hardcoding.

Usage:
    python generate_tests.py <FileToTest.cs>
    python generate_tests.py <FileToTest.cs> --manifest scan_output.json
    python generate_tests.py <FileToTest.cs> --project MyApp
    python generate_tests.py <FileToTest.cs> --model gemini-3.6-flash --coverage 90 --retries 4
"""
import argparse
import json
import sys
from pathlib import Path

from testgen.agent_loop import TestGenLoop
from testgen.author import AllKeysRateLimitedError
from testgen.context import ContextBuilder
from testgen.logger import get_current_log_file, get_logger, setup_logger


def find_project(manifest: dict, project_hint: str = None) -> dict:
    """Select the source project from the manifest.
    If --project is given, match by name (case-insensitive).
    Otherwise, auto-select if there is exactly one non-test project.
    """
    logger = get_logger()
    projects = manifest.get("projects", [])
    if not projects:
        logger.error("[ERROR] No projects found in manifest. Run scan_project.py first.")
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

    # Multiple projects -- list them and ask user to pick
    logger.info("[INFO] Multiple projects found in manifest:")
    for i, p in enumerate(projects, 1):
        logger.info(f"  {i}. {p['project_name']} ({p['csproj']})")
    logger.error("Re-run with --project <name> to select one.")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="AI-powered C# unit test generator (any .NET 8 project)")
    parser.add_argument("target_file", help="Name or relative path of the C# file to generate tests for")
    parser.add_argument("--manifest", default="scan_output.json", help="Path to the scan manifest JSON (default: scan_output.json)")
    parser.add_argument("--project", default=None, help="Project name when manifest has multiple projects")
    parser.add_argument("--model", default="gemini-3.6-flash", help="Gemini model to use (default: gemini-3.6-flash)")
    parser.add_argument("--coverage", type=float, default=90.0, help="Target coverage %% (default: 90)")
    parser.add_argument("--retries", type=int, default=4, help="Max author/critic iterations (default: 4)")
    args = parser.parse_args()

    # Initialize centralized logger to Documents/Logs/
    target_stem = Path(args.target_file).stem
    logger = setup_logger(session_name=f"testgen_{target_stem}")

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        logger.error(f"[ERROR] Manifest not found at '{manifest_path}'. Run scan_project.py first.")
        sys.exit(1)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    project = find_project(manifest, args.project)
    root = Path(manifest["root"]).resolve()
    project_name = project["project_name"]
    test_csproj = root / "tests" / f"{project_name}.Tests" / f"{project_name}.Tests.csproj"

    context_builder = ContextBuilder(manifest)
    loop = TestGenLoop(
        context_builder=context_builder,
        test_csproj=test_csproj,
        model_name=args.model,
        target_coverage_pct=args.coverage,
        max_retries=args.retries,
    )

    try:
        result = loop.process_file(args.target_file)
        logger.info("\n" + "=" * 50)
        logger.info("[SUMMARY] EXECUTION SUMMARY:")
        logger.info(json.dumps(result, indent=2))
        logger.info(f"[LOG SAVED] Log file written to: {get_current_log_file()}")
        logger.info("=" * 50)
    except AllKeysRateLimitedError:
        logger.warning("\n[RATE LIMIT EXHAUSTED] All Gemini API keys have hit their daily quota limit.")
        logger.info(f"Progress has been logged to: {get_current_log_file()}")
        logger.info("Run again later to resume!")
        sys.exit(0)
    except Exception as e:
        logger.error(f"\n[FATAL ERROR] Unexpected exception: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
