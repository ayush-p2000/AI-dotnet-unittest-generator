import json
import os
import stat
import sys
from pathlib import Path

from testgen.scanner import scan_project

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scan_project.py <path-to-target-project>")
        sys.exit(1)

    target = sys.argv[1]
    manifest = scan_project(target)

    out_file = Path("scan_output.json")
    if out_file.exists():
        try:
            os.chmod(out_file, stat.S_IWRITE | stat.S_IREAD)
        except Exception:
            pass

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    total_files = sum(len(p["files"]) for p in manifest["projects"])
    total_types = sum(len(f["types"]) for p in manifest["projects"] for f in p["files"])
    print(f"\nFound {len(manifest['projects'])} project(s), {total_files} file(s), {total_types} type(s).")
    for p in manifest["projects"]:
        print(f"  - {p['project_name']}: {len(p['files'])} file(s)")
    print("Written to scan_output.json")