import json
import sys
from testgen.context import ContextBuilder

if __name__ == "__main__":
    with open("scan_output.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    builder = ContextBuilder(manifest)
    
    # Pick a file or take from CLI
    target_file = sys.argv[1] if len(sys.argv) > 1 else "DistanceCalculator.cs"
    context = builder.build_prompt_context(target_file)
    
    print(f"=== File: {context['file_name']} ===")
    print(f"Namespace: {context['namespace']}")
    print(f"Usings: {context['usings']}")
    print(f"Types in File: {[t['name'] for t in context['types']]}")
    print(f"Resolved Dependencies Count: {len(context['resolved_dependencies'])}")
    print("\n--- Formatted Dependencies Context for AI Agent ---")
    print(context["formatted_dependencies_context"])
    print("\n--- Source Code Sample (first 10 lines) ---")
    print("\n".join(context["source_code"].splitlines()[:10]))
