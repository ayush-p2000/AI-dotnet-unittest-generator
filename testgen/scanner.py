import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

from testgen.logger import get_logger

EXCLUDE_DIRS = {
    "bin", "obj", ".venv", ".git", ".github", ".vs", "node_modules", "packages",
    "tests", "test", "samples", "sample", "benchmarks", "benchmark", "demos", "demo", "docs"
}
EXCLUDE_FILE_SUFFIXES = (".Designer.cs", ".g.cs", ".g.i.cs", "AssemblyInfo.cs")

# Regex for namespaces and usings
NAMESPACE_RE = re.compile(r"(?:^|\s)namespace\s+([\w\.]+)", re.MULTILINE)
USING_RE = re.compile(r"(?:^|\s)(?:global\s+)?using\s+(?:static\s+)?(?:[\w\.]+\s*=\s*)?([\w\.]+)\s*;", re.MULTILINE)

# Type declaration: modifiers, kind, name, optional generics, optional primary ctor, optional bases, and open brace or semicolon
TYPE_DECL_RE = re.compile(
    r"(?P<mods>(?:public|internal|private|protected|static|sealed|abstract|partial|readonly)\s+)*"
    r"(?P<kind>class|interface|struct|record(?:\s+class|\s+struct)?|enum)\s+"
    r"(?P<name>\w+)"
    r"(?P<generics><[^>]+>)?"
    r"(?:\s*\((?P<primary_ctor>[^)]*)\))?"
    r"(?:\s*:\s*(?P<bases>[^\{;]+))?"
    r"\s*(?P<open>\{|;)",
    re.MULTILINE,
)

# Method regex (block body or expression body)
METHOD_RE = re.compile(
    r"(?P<mods>(?:public|protected|internal|private|static|virtual|override|abstract|async|extern|new)\s+)+"
    r"(?P<return>[\w<>\[\],\.\?\s]+?)\s+"
    r"(?P<name>\w+)"
    r"(?P<generics><[^>]+>)?"
    r"\s*\((?P<params>[^)]*)\)"
    r"\s*(?:where\s+[\w\s:<>,]+)?\s*"
    r"(?:\{|=>)",
    re.MULTILINE,
)

# Interface method regex
INTERFACE_METHOD_RE = re.compile(
    r"^\s*(?P<return>[\w<>\[\],\.\?\s]+?)\s+"
    r"(?P<name>\w+)"
    r"(?P<generics><[^>]+>)?"
    r"\s*\((?P<params>[^)]*)\)\s*"
    r"(?:where\s+[\w\s:<>,]+)?\s*;",
    re.MULTILINE,
)

# Constructor regex
CONSTRUCTOR_RE = re.compile(
    r"(?P<mods>(?:public|protected|internal|private)\s+)"
    r"(?P<name>\w+)\s*"
    r"\((?P<params>[^)]*)\)\s*"
    r"(?::\s*(?:this|base)\s*\([^)]*\)\s*)?"
    r"(?:\{|=>)",
    re.MULTILINE,
)

# Property regex: matches auto-properties, full properties, and expression-bodied properties
PROPERTY_RE = re.compile(
    r"^\s*(?P<mods>(?:public|protected|internal|private|static|virtual|override|abstract|required|readonly)\s+)+"
    r"(?P<type>[\w<>\[\],\.\?\s]+?)\s+"
    r"(?P<name>\w+)\s*"
    r"(?:\{\s*(?P<accessors>[^}]+)\s*\}|=>\s*(?P<expr>[^;]+);|=(?P<init_val>[^;]+);)",
    re.MULTILINE,
)

# Constant / Field regex
FIELD_RE = re.compile(
    r"^\s*(?P<mods>(?:public|protected|internal|private|static|readonly|const|volatile)\s+)+"
    r"(?P<type>[\w<>\[\],\.\?\s]+?)\s+"
    r"(?P<name>\w+)\s*"
    r"(?:=\s*(?P<val>[^;]+))?\s*;",
    re.MULTILINE,
)

# Enum member regex
ENUM_MEMBER_RE = re.compile(
    r"^\s*(?P<name>\w+)(?:\s*=\s*(?P<value>[^,\n\r]+))?",
    re.MULTILINE,
)


def find_matching_brace(text: str, open_index: int) -> int:
    """
    Given the index of an opening '{', return the index of its matching '}'.
    Bug 5 fix: Properly handles verbatim strings (@"..."), interpolated strings, and normal strings.
    """
    depth = 0
    in_string = False
    in_verbatim_string = False
    in_char = False
    in_line_comment = False
    in_block_comment = False
    quote_char = ""
    i = open_index
    length = len(text)

    while i < length:
        ch = text[i]
        prev_ch = text[i - 1] if i > 0 else ""
        next_ch = text[i + 1] if i + 1 < length else ""

        # Handle line comments
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        # Handle block comments
        if in_block_comment:
            if ch == "*" and next_ch == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        # Check comment starts when outside strings
        if not in_string and not in_verbatim_string and not in_char:
            if ch == "/" and next_ch == "/":
                in_line_comment = True
                i += 2
                continue
            if ch == "/" and next_ch == "*":
                in_block_comment = True
                i += 2
                continue

            # Check verbatim string start: @" or $@'
            if ch == "@" and next_ch == '"':
                in_verbatim_string = True
                i += 2
                continue
            if ch == "$" and next_ch == "@" and i + 2 < length and text[i + 2] == '"':
                in_verbatim_string = True
                i += 3
                continue

            # Check normal string or char
            if ch == '"':
                in_string = True
                quote_char = '"'
                i += 1
                continue
            elif ch == "'":
                in_char = True
                quote_char = "'"
                i += 1
                continue
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
            continue

        # Handle verbatim string end ("" escapes quote in verbatim)
        if in_verbatim_string:
            if ch == '"':
                if next_ch == '"':
                    i += 2  # Escaped double quote
                    continue
                else:
                    in_verbatim_string = False
            i += 1
            continue

        # Handle standard string and char
        if in_string or in_char:
            if ch == quote_char and prev_ch != "\\":
                in_string = False
                in_char = False
            i += 1
            continue

        i += 1

    return length - 1


def clean_type_name(t: str) -> str:
    """Strip unnecessary whitespace while keeping generics intact."""
    return re.sub(r"\s+", " ", t).strip()


def extract_enum_members(body: str) -> List[Dict[str, Any]]:
    members = []
    lines = body.split("\n")
    for line in lines:
        line = line.strip().rstrip(",")
        if not line or line.startswith("//") or line.startswith("/*") or line.startswith("["):
            continue
        m = ENUM_MEMBER_RE.match(line)
        if m and m.group("name"):
            name = m.group("name")
            val = m.group("value")
            members.append({
                "name": name,
                "value": val.strip() if val else None
            })
    return members


def extract_properties(body: str) -> List[Dict[str, Any]]:
    props = []
    for m in PROPERTY_RE.finditer(body):
        mods = (m.group("mods") or "").strip()
        prop_type = clean_type_name(m.group("type"))
        name = m.group("name")
        accessors = m.group("accessors")
        expr = m.group("expr")
        init_val = m.group("init_val")

        # Skip if it looks like a method or constructor artifact
        if "(" in name or ")" in name:
            continue

        props.append({
            "modifiers": mods,
            "type": prop_type,
            "name": name,
            "has_getter": bool(accessors and "get" in accessors) or bool(expr),
            "has_setter": bool(accessors and ("set" in accessors or "init" in accessors)),
            "is_expression_bodied": bool(expr),
            "initial_value": (init_val or expr or "").strip() or None,
        })
    return props


def extract_fields_and_constants(body: str) -> List[Dict[str, Any]]:
    fields = []
    for m in FIELD_RE.finditer(body):
        mods = (m.group("mods") or "").strip()
        field_type = clean_type_name(m.group("type"))
        name = m.group("name")
        val = m.group("val")

        # Exclude event / delegate declarations and properties
        if "event" in mods or "(" in name or "{" in name:
            continue

        fields.append({
            "modifiers": mods,
            "type": field_type,
            "name": name,
            "is_const": "const" in mods,
            "is_static": "static" in mods,
            "is_readonly": "readonly" in mods,
            "value": val.strip() if val else None,
        })
    return fields


def extract_methods_from_body(kind: str, type_name: str, body: str) -> List[Dict[str, Any]]:
    if "enum" in kind:
        return []
    methods = []
    if kind == "interface":
        for m in INTERFACE_METHOD_RE.finditer(body):
            methods.append({
                "modifiers": "public",
                "return_type": clean_type_name(m.group("return")),
                "name": m.group("name"),
                "generics": m.group("generics") or None,
                "params": m.group("params").strip(),
            })
    else:
        for m in METHOD_RE.finditer(body):
            name = m.group("name")
            if name == type_name:
                continue  # constructors are handled separately
            mods = (m.group("mods") or "").strip()
            methods.append({
                "modifiers": mods,
                "return_type": clean_type_name(m.group("return")),
                "name": name,
                "generics": m.group("generics") or None,
                "params": m.group("params").strip(),
            })
    return methods


def extract_constructors_from_body(type_name: str, primary_ctor_params: str, body: str) -> List[Dict[str, Any]]:
    ctors = []
    if primary_ctor_params:
        ctors.append({
            "modifiers": "public",
            "params": primary_ctor_params.strip(),
            "style": "primary"
        })
    for m in CONSTRUCTOR_RE.finditer(body):
        if m.group("name") == type_name:
            ctors.append({
                "modifiers": (m.group("mods") or "").strip(),
                "params": m.group("params").strip(),
                "style": "traditional"
            })
    return ctors


def extract_types_with_bodies(text: str) -> List[Dict[str, Any]]:
    results = []
    for m in TYPE_DECL_RE.finditer(text):
        if m.group("open") == ";":
            body = ""  # bodyless record or interface
        else:
            open_idx = m.end() - 1
            close_idx = find_matching_brace(text, open_idx)
            body = text[open_idx + 1:close_idx]

        bases_raw = m.group("bases")
        bases = []
        if bases_raw:
            # Strip any 'where ...' generic constraint that might be attached
            bases_cleaned = re.sub(r"\s+where\s+.*", "", bases_raw.strip(), flags=re.DOTALL)
            bases = [clean_type_name(b) for b in bases_cleaned.split(",") if b.strip()]

        results.append({
            "modifiers": (m.group("mods") or "").strip(),
            "kind": m.group("kind").strip(),
            "name": m.group("name"),
            "generics": m.group("generics") or None,
            "bases": bases,
            "primary_ctor_params": (m.group("primary_ctor") or "").strip(),
            "body": body,
        })
    return results


def extract_file_info(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig", errors="ignore")

    namespace_match = NAMESPACE_RE.search(text)
    usings = list(dict.fromkeys(USING_RE.findall(text)))

    types = []
    for t in extract_types_with_bodies(text):
        kind = t["kind"]
        type_name = t["name"]
        body = t["body"]

        type_info = {
            "modifiers": t["modifiers"],
            "kind": kind,
            "name": type_name,
            "generics": t["generics"],
            "bases": t["bases"],
            "constructors": extract_constructors_from_body(
                type_name, t["primary_ctor_params"], body
            ),
            "properties": extract_properties(body) if "enum" not in kind else [],
            "fields": extract_fields_and_constants(body) if "enum" not in kind else [],
            "methods": extract_methods_from_body(kind, type_name, body),
            "enum_members": extract_enum_members(body) if "enum" in kind else [],
        }
        types.append(type_info)

    return {
        "file_name": path.name,
        "path": str(path),
        "namespace": namespace_match.group(1) if namespace_match else None,
        "usings": usings,
        "types": types,
    }


def parse_csproj_metadata(csproj_path: Path) -> Dict[str, Any]:
    """Parse .csproj file to extract target framework, package references, and project references."""
    packages = []
    project_references = []
    target_framework = "net8.0"

    try:
        tree = ET.parse(csproj_path)
        root = tree.getroot()

        tf = root.find(".//TargetFramework")
        if tf is not None and tf.text:
            target_framework = tf.text.strip()
        else:
            tfs = root.find(".//TargetFrameworks")
            if tfs is not None and tfs.text:
                target_framework = tfs.text.strip()

        for pkg in root.findall(".//PackageReference"):
            inc = pkg.get("Include") or pkg.get("Update")
            ver = pkg.get("Version") or ""
            if not ver:
                ver_elem = pkg.find("Version")
                if ver_elem is not None and ver_elem.text:
                    ver = ver_elem.text.strip()
            if inc:
                packages.append({"name": inc, "version": ver})

        for proj in root.findall(".//ProjectReference"):
            inc = proj.get("Include")
            if inc:
                project_references.append(inc)

    except Exception:
        # Fallback to basic regex if XML parsing fails
        text = csproj_path.read_text(encoding="utf-8", errors="ignore")
        tf_match = re.search(r"<TargetFramework>\s*([^<\s]+)\s*</TargetFramework>", text)
        if tf_match:
            target_framework = tf_match.group(1)

    return {
        "target_framework": target_framework,
        "packages": packages,
        "project_references": project_references,
    }


def is_test_project(csproj_path: Path) -> bool:
    name_lower = csproj_path.stem.lower()
    # If the project name explicitly indicates tests
    if name_lower.endswith((".tests", ".test", ".unittests", ".unittest", ".integrationtests")):
        return True

    text = csproj_path.read_text(encoding="utf-8", errors="ignore").lower()

    # Web, Worker, and App projects are NEVER test projects even if packages like xunit were accidentally installed
    if 'sdk="microsoft.net.sdk.web"' in text or 'sdk="microsoft.net.sdk.worker"' in text or 'sdk="microsoft.net.sdk.blazorwebassembly"' in text:
        return False

    # Explicit MSBuild test project flag
    if "<istestproject>true</istestproject>" in text:
        return True

    # Check for test runner SDK
    if "microsoft.net.test.sdk" in text:
        return True

    return False



def find_csproj_files(root: Path) -> List[Path]:
    """
    Bug 10 fix: Filters out excluded directories relative to root rather than absolute parts.
    """
    results = []
    for p in root.rglob("*.csproj"):
        try:
            rel_parts = set(p.relative_to(root).parts)
            if not (EXCLUDE_DIRS & rel_parts):
                results.append(p)
        except Exception:
            if not (EXCLUDE_DIRS & set(p.parts)):
                results.append(p)
    return results


def find_cs_files_for_project(csproj_path: Path) -> List[Path]:
    project_dir = csproj_path.parent
    files = []
    for p in project_dir.rglob("*.cs"):
        try:
            rel_parts = set(p.relative_to(project_dir).parts)
            if EXCLUDE_DIRS & rel_parts:
                continue
        except Exception:
            if EXCLUDE_DIRS & set(p.parts):
                continue
        if p.name.endswith(EXCLUDE_FILE_SUFFIXES):
            continue
        files.append(p)
    return files


def build_symbol_table(projects: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Builds a global symbol lookup table across all scanned projects."""
    symbols = {}
    for proj in projects:
        proj_name = proj["project_name"]
        for f in proj["files"]:
            file_path = f["path"]
            namespace = f["namespace"]
            for t in f["types"]:
                t_name = t["name"]
                symbols[t_name] = {
                    "kind": t["kind"],
                    "namespace": namespace,
                    "project": proj_name,
                    "file_path": file_path,
                    "bases": t["bases"],
                    "constructors": t["constructors"],
                    "properties": [
                        {"name": p["name"], "type": p["type"], "modifiers": p["modifiers"]}
                        for p in t["properties"]
                    ],
                    "fields": [
                        {"name": fld["name"], "type": fld["type"], "modifiers": fld["modifiers"], "value": fld["value"]}
                        for fld in t["fields"]
                    ],
                    "methods": [
                        {"name": m["name"], "return_type": m["return_type"], "params": m["params"], "modifiers": m["modifiers"]}
                        for m in t["methods"]
                    ],
                    "enum_members": t["enum_members"],
                }
    return symbols


def scan_project(root: str) -> Dict[str, Any]:
    logger = get_logger()
    root_path = Path(root).resolve()
    csproj_files = find_csproj_files(root_path)

    manifest: Dict[str, Any] = {
        "root": str(root_path),
        "projects": [],
        "symbol_table": {}
    }

    for csproj in csproj_files:
        if is_test_project(csproj):
            logger.info(f"Skipping test project during scan: {csproj.name}")
            continue

        meta = parse_csproj_metadata(csproj)
        cs_files = find_cs_files_for_project(csproj)

        manifest["projects"].append({
            "csproj": str(csproj),
            "project_name": csproj.stem,
            "target_framework": meta["target_framework"],
            "packages": meta["packages"],
            "project_references": meta["project_references"],
            "files": [extract_file_info(p) for p in cs_files],
        })

    manifest["symbol_table"] = build_symbol_table(manifest["projects"])
    return manifest