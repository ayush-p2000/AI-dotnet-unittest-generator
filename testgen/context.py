import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from testgen.logger import get_logger


def extract_type_tokens(signature_or_text: str) -> Set[str]:
    """Extract potential C# type identifiers from parameters, return types, or base types."""
    # Remove strings and comments
    cleaned = re.sub(r'".*?"|\'.*?\'|//.*|/\*.*?\*/', "", signature_or_text)
    # Match words that could be C# type names (PascalCase or starting with I for interfaces)
    tokens = re.findall(r"\b[A-Z][a-zA-Z0-9_]*\b", cleaned)
    # Exclude common C# keywords or non-custom types
    system_types = {
        "Task", "ValueTask", "ActionResult", "IActionResult", "List", "Dictionary",
        "IEnumerable", "ICollection", "IList", "IReadOnlyList", "Nullable", "String",
        "Int32", "Int64", "Boolean", "DateTime", "DateOnly", "TimeOnly", "TimeSpan",
        "Guid", "Uri", "Object", "CancellationToken", "HttpContext", "ClaimsPrincipal",
        "Fact", "Theory", "InlineData", "Mock", "Assert", "Should", "Action", "Func"
    }
    return {t for t in tokens if t not in system_types}


class ContextBuilder:
    def __init__(self, manifest: Dict[str, Any]):
        self.manifest = manifest
        self.symbol_table = manifest.get("symbol_table", {})
        self.projects = manifest.get("projects", [])
        self.root = manifest.get("root", "")
        self.logger = get_logger()

    def find_file_and_project(self, file_path_or_name: str) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        target = Path(file_path_or_name).resolve()
        target_name = target.name.lower()

        for project in self.projects:
            for f in project["files"]:
                p = Path(f["path"]).resolve()
                if p == target or p.name.lower() == target_name:
                    return f, project
        return None, None

    def find_file_info(self, file_path_or_name: str) -> Optional[Dict[str, Any]]:
        f, _ = self.find_file_and_project(file_path_or_name)
        return f

    def resolve_dependencies(self, file_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Identifies all types needed to write tests for this file:
        - Constructor injected dependencies (interfaces/services)
        - Method parameter and return types (DTOs, models, entities)
        - Base classes and interfaces implemented
        - Enums referenced
        """
        discovered_types: Set[str] = set()

        for t in file_info.get("types", []):
            # 1. Base classes and implemented interfaces
            for base in t.get("bases", []):
                discovered_types.update(extract_type_tokens(base))

            # 2. Constructor parameters (Injected dependencies)
            for ctor in t.get("constructors", []):
                discovered_types.update(extract_type_tokens(ctor.get("params", "")))

            # 3. Method return types and parameters
            for m in t.get("methods", []):
                discovered_types.update(extract_type_tokens(m.get("return_type", "")))
                discovered_types.update(extract_type_tokens(m.get("params", "")))

            # 4. Property types
            for p in t.get("properties", []):
                discovered_types.update(extract_type_tokens(p.get("type", "")))

        # 2nd pass: For all resolved types (like DbContext), extract types from their DbSets and properties
        secondary_types: Set[str] = set()
        for t_name in list(discovered_types):
            if t_name in self.symbol_table:
                sym = self.symbol_table[t_name]
                for prop in sym.get("properties", []):
                    secondary_types.update(extract_type_tokens(prop.get("type", "")))
                for meth in sym.get("methods", []):
                    secondary_types.update(extract_type_tokens(meth.get("params", "")))
                    secondary_types.update(extract_type_tokens(meth.get("return_type", "")))

        discovered_types.update(secondary_types)

        # Resolve all discovered types from the symbol table
        resolved_symbols: Dict[str, Any] = {}
        for type_name in discovered_types:
            if type_name in self.symbol_table:
                resolved_symbols[type_name] = self.symbol_table[type_name]

        return resolved_symbols

    def build_prompt_context(self, file_path_or_name: str) -> Dict[str, Any]:
        file_info, project_info = self.find_file_and_project(file_path_or_name)
        if not file_info:
            raise FileNotFoundError(f"File '{file_path_or_name}' not found in scan manifest.")

        raw_path = Path(file_info["path"]).resolve()

        # Bug 9 fix: wrap read in try/except with fallback
        try:
            source_code = raw_path.read_text(encoding="utf-8-sig", errors="ignore")
        except Exception as e:
            self.logger.warning(f"Could not read source file from disk '{raw_path}': {e}")
            source_code = f"// Source file {raw_path.name} could not be read directly from disk."

        dependencies = self.resolve_dependencies(file_info)

        # Bug 2 fix: compute relative sub-folder and relative path safely with try/except
        sub_folder = ""
        if project_info and project_info.get("csproj"):
            proj_dir = Path(project_info["csproj"]).parent.resolve()
            try:
                rel_dir = raw_path.parent.relative_to(proj_dir)
                sub_folder = "" if str(rel_dir) == "." else str(rel_dir)
            except Exception:
                sub_folder = ""

        # Safe relative path calculation
        try:
            root_path = Path(self.root).resolve()
            rel_path = str(raw_path.relative_to(root_path))
        except Exception:
            rel_path = raw_path.name

        # Format dependency summaries for Gemini with their exact source code snippets
        formatted_deps = []
        for name, info in dependencies.items():
            kind = info.get("kind", "type")
            ns = info.get("namespace") or "global"
            file_p = info.get("file_path")

            dep_block = [f"### {kind.capitalize()}: `{name}` (Namespace: `{ns}`)"]
            if file_p and Path(file_p).exists():
                try:
                    dep_code = Path(file_p).read_text(encoding="utf-8-sig", errors="ignore")
                    dep_block.append(f"```csharp\n{dep_code.strip()}\n```")
                except Exception:
                    pass
            else:
                if info.get("properties"):
                    prop_strs = [f"{p['modifiers']} {p['type']} {p['name']}" for p in info["properties"][:15]]
                    dep_block.append("Properties:\n  - " + "\n  - ".join(prop_strs))
                if info.get("methods"):
                    method_strs = [f"{m['modifiers']} {m['return_type']} {m['name']}({m['params']})" for m in info["methods"]]
                    dep_block.append("Methods:\n  - " + "\n  - ".join(method_strs))

            formatted_deps.append("\n".join(dep_block))

        return {
            "file_name": file_info["file_name"],
            "full_path": str(raw_path),
            "relative_path": rel_path,
            "sub_folder": sub_folder,
            "namespace": file_info["namespace"],
            "usings": file_info["usings"],
            "types": file_info["types"],
            "source_code": source_code,
            "resolved_dependencies": dependencies,
            "formatted_dependencies_context": "\n\n".join(formatted_deps) if formatted_deps else "No internal dependencies needed."
        }
