import argparse
import json
import os
import stat
import sys
from pathlib import Path

from testgen.scanner import scan_project

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scan a .NET project or solution directory and generate an AST scan manifest."
    )
    parser.add_argument(
        "target",
        help="Path to target .NET project or solution directory"
    )
    parser.add_argument(
        "--scanner",
        "--engine",
        dest="scanner",
        choices=["auto", "roslyn", "regex"],
        default="auto",
        help="Scanner engine: 'auto' (use Roslyn if available, fallback to Regex), 'roslyn' (Microsoft.CodeAnalysis AST), or 'regex' (built-in)"
    )
    parser.add_argument(
        "--output",
        "-o",
        dest="output",
        default="scan_output.json",
        help="Path to save the scan manifest JSON (default: scan_output.json)"
    )

    args = parser.parse_args()

    manifest = scan_project(args.target, engine=args.scanner)

    out_file = Path(args.output)
    if out_file.exists():
        try:
            os.chmod(out_file, stat.S_IWRITE | stat.S_IREAD)
        except Exception:
            pass

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    engine_used = manifest.get("scanner_engine", args.scanner)
    total_files = sum(len(p["files"]) for p in manifest.get("projects", []))
    total_types = sum(len(f.get("types", [])) for p in manifest.get("projects", []) for f in p.get("files", []))
    print(f"\n[Scanner: {engine_used.upper()}] Found {len(manifest.get('projects', []))} project(s), {total_files} file(s), {total_types} type(s).")
    for p in manifest.get("projects", []):
        print(f"  - {p['project_name']}: {len(p['files'])} file(s)")
    print(f"Written to {out_file}")