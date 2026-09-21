#!/usr/bin/env python3
"""
CLI Tool to connect to SonarQube, fetch Cognitive Complexity issues (S3776),
and resolve them using a 2-agent loop (Refactor Generator + Build/Test Verifier).
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from sonar.agent_loop import SonarRefactorLoop
from sonar.connector import SonarConnector, SonarConnectionError
from sonar.matcher import IssueMatcher
from sonar.refactor_agent import RefactorAgent
from sonar.state import SonarStateTracker
from sonar.verifier_agent import VerifierAgent

load_dotenv()


def main():
    parser = argparse.ArgumentParser(
        description="Auto-resolve SonarQube Cognitive Complexity (S3776) issues via Dual-Agent AI Loop"
    )
    parser.add_argument("--host", help="SonarQube host URL (default: from .env SONAR_HOST_URL or http://localhost:9000)")
    parser.add_argument("--token", help="SonarQube API token (default: from .env SONAR_TOKEN)")
    parser.add_argument("--project-key", help="SonarQube project key (default: from .env SONAR_PROJECT_KEY)")
    parser.add_argument("--target", default=".", help="Path to local solution root or .sln/.csproj (default: .)")
    parser.add_argument("--branch", help="SonarQube branch name")
    parser.add_argument("--file", help="Filter to only resolve issues in this file")
    parser.add_argument("--dry-run", action="store_true", help="Locate methods and show metrics without modifying files")
    parser.add_argument("--no-tests", action="store_true", help="Verify with 'dotnet build' only, skip 'dotnet test'")
    parser.add_argument("--resume", action="store_true", help="Skip already resolved issues in state file")
    parser.add_argument("--max-retries", type=int, default=3, help="Max self-healing attempts per method (default: 3)")
    parser.add_argument("--provider", default="auto", choices=["auto", "ollama", "gemini", "qwen-cloud"], help="AI provider agent (default: auto)")
    parser.add_argument("--model", default=None, help="AI model name (default: auto-detected based on provider)")

    args = parser.parse_args()

    # Determine connection settings
    host = args.host or os.getenv("SONAR_HOST_URL") or "http://localhost:9000"
    token = args.token or os.getenv("SONAR_TOKEN")
    project_key = args.project_key or os.getenv("SONAR_PROJECT_KEY")

    if not project_key:
        print("Error: SonarQube project key must be specified via --project-key or SONAR_PROJECT_KEY in .env.")
        sys.exit(1)

    target_path = Path(args.target).resolve()
    print("======================================================================")
    print("  SONARQUBE COGNITIVE COMPLEXITY RESOLVER & DUAL-AGENT AUTO-FIXER")
    print("======================================================================")
    print(f"SonarQube Host:  {host}")
    print(f"Project Key:     {project_key}")
    print(f"Local Workspace: {target_path}")
    print(f"Mode:            {'DRY-RUN' if args.dry_run else 'ACTIVE AUTO-FIX'}")
    print("----------------------------------------------------------------------")

    # 1. Connect to SonarQube
    connector = SonarConnector(host_url=host, token=token)
    try:
        val = connector.validate_connection()
        if not val.get("connected"):
            print(f"Warning: Could not connect to SonarQube at {host}.")
        print(f"Connected: {val.get('connected')} | Server Version: {val.get('server_version')}")
    except SonarConnectionError as e:
        print(f"SonarQube connection warning: {e}")

    # 2. Fetch Cognitive Complexity issues
    print(f"\nFetching Cognitive Complexity issues for '{project_key}'...")
    try:
        issues = connector.get_cognitive_complexity_issues(
            project_key=project_key,
            branch=args.branch,
        )
    except Exception as e:
        print(f"Failed to fetch issues from SonarQube: {e}")
        sys.exit(1)

    if args.file:
        issues = [i for i in issues if args.file.lower() in i.file_path.lower()]

    print(f"Found {len(issues)} unresolved Cognitive Complexity (S3776) issue(s).")
    if not issues:
        print("No issues to process. Quality Gate is clear of S3776 code smells!")
        return

    # 3. Setup agents and matcher
    state_tracker = SonarStateTracker()
    matcher = IssueMatcher(target_path)
    refactor_agent = RefactorAgent(model_name=args.model, provider=args.provider)
    verifier_agent = VerifierAgent(target_path)
    loop = SonarRefactorLoop(
        matcher=matcher,
        verifier_agent=verifier_agent,
        refactor_agent=refactor_agent,
        max_retries=args.max_retries,
    )

    resolved_count = 0
    failed_count = 0

    for idx, issue in enumerate(issues, 1):
        stats = issue.complexity_stats
        print(f"\n[{idx}/{len(issues)}] Processing Issue: {issue.key}")
        print(f"  Component: {issue.file_path}:{issue.line}")
        print(f"  Complexity: {stats['current']} (Max Allowed: {stats['allowed']})")

        if args.resume and state_tracker.is_resolved(issue.key):
            print("  [SKIP] Issue already verified in previous run.")
            resolved_count += 1
            continue

        result = loop.resolve_issue(
            issue=issue,
            dry_run=args.dry_run,
            run_tests=not args.no_tests,
        )

        status = result.get("status", "UNKNOWN")
        method_name = result.get("method", "UnknownMethod")
        attempts = result.get("iterations", 1)

        if status == "VERIFIED":
            resolved_count += 1
            state_tracker.record_issue_result(
                issue_key=issue.key,
                file_path=issue.file_path,
                method_name=method_name,
                status=status,
                initial_complexity=stats["current"],
                allowed_complexity=stats["allowed"],
                attempts=attempts,
            )
        elif status == "DRY_RUN":
            pass
        else:
            failed_count += 1
            state_tracker.record_issue_result(
                issue_key=issue.key,
                file_path=issue.file_path,
                method_name=method_name,
                status=status,
                initial_complexity=stats["current"],
                allowed_complexity=stats["allowed"],
                attempts=attempts,
                error_summary=str(result.get("errors", result.get("message"))),
            )

    print("\n======================================================================")
    print("  EXECUTION COMPLETE")
    print(f"  Total Processed: {len(issues)}")
    print(f"  Verified / Resolved: {resolved_count}")
    print(f"  Failed / Rolled back: {failed_count}")
    print("======================================================================")


if __name__ == "__main__":
    main()
