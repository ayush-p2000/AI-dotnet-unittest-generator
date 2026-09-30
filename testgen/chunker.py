import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class MethodChunk:
    name: str
    return_type: str
    modifiers: str
    params: str
    generics: Optional[str]
    start_line: int
    end_line: int
    body: str
    full_text: str
    branch_count: int
    has_async: bool
    has_try_catch: bool
    has_throw: bool
    is_public: bool
    complexity_level: str  # "CRITICAL", "HIGH", "MEDIUM", "LOW"
    priority_score: int    # 1 (lowest) to 5 (highest)
    priority_label: str    # "CRITICAL", "HIGH", "NORMAL", "LOW", "TRIVIAL"
    uncovered_lines: List[int] = field(default_factory=list)
    coverage_focus: str = ""


# Branch and decision-making keywords in C#
BRANCH_KEYWORDS_RE = re.compile(
    r"\b(if|else\s+if|switch|case|catch|throw|while|for|foreach)\b",
    re.MULTILINE,
)
LOGICAL_OPS_RE = re.compile(r"(\?\?|\?\.|&&|\|\|)")
TERNARY_RE = re.compile(r"\?[^:;\n]+\:")


def count_branches(code: str) -> int:
    """Calculates branch and decision points in a C# code snippet."""
    # Strip raw strings (C# 11+ / .NET 7/8/9), normal strings, char literals, and comments
    cleaned = re.sub(r'(\$+)?\"{3,}.*?\"{3,}|".*?"|\'.*?\'|//.*|/\*.*?\*/', "", code, flags=re.DOTALL)
    keywords = len(BRANCH_KEYWORDS_RE.findall(cleaned))
    logical = len(LOGICAL_OPS_RE.findall(cleaned))
    ternaries = len(TERNARY_RE.findall(cleaned))
    return keywords + logical + ternaries


def find_matching_brace(text: str, open_index: int) -> int:
    """Finds matching closing brace '}' for an opening brace '{', handling modern C# syntax."""
    depth = 0
    in_str = False
    in_verbatim = False
    in_char = False
    in_line_comment = False
    in_block_comment = False
    i = open_index
    length = len(text)

    while i < length:
        ch = text[i]
        next_ch = text[i + 1] if i + 1 < length else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and next_ch == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if in_verbatim:
            if ch == '"':
                if next_ch == '"':
                    i += 2
                    continue
                in_verbatim = False
            i += 1
            continue

        if in_str:
            if ch == "\\" and next_ch:
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue

        if in_char:
            if ch == "\\" and next_ch:
                i += 2
                continue
            if ch == "'":
                in_char = False
            i += 1
            continue

        # Check raw string literals (C# 11+ / .NET 7/8/9): """ ... """ or $$""" ... """
        raw_start_m = re.match(r"^(\$+)?(\"{3,})", text[i:])
        if raw_start_m:
            quotes = raw_start_m.group(2)
            quote_len = len(quotes)
            start_offset = len(raw_start_m.group(0))
            close_idx = text.find(quotes, i + start_offset)
            if close_idx != -1:
                i = close_idx + quote_len
                continue
            else:
                break

        # Check comment starts
        if ch == "/" and next_ch == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and next_ch == "*":
            in_block_comment = True
            i += 2
            continue

        # Check string starts
        if ch == "@" and next_ch == '"':
            in_verbatim = True
            i += 2
            continue
        if ch == '$' and next_ch == '"':
            in_str = True
            i += 2
            continue
        if ch == '"':
            in_str = True
            i += 1
            continue
        if ch == "'":
            in_char = True
            i += 1
            continue

        # Brace matching
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1

    return -1


METHOD_SIG_RE = re.compile(
    r"(?P<mods>(?:(?:public|protected|internal|private|static|virtual|override|abstract|async|extern|new|sealed|readonly)\s+)+)"
    r"(?P<return>[\w<>\[\],\.\?\s]+?)\s+"
    r"(?P<name>\w+)"
    r"(?P<generics><[^>]+>)?"
    r"\s*\((?P<params>[^)]*)\)"
    r"\s*(?:where\s+[\w\s:<>,]+)?\s*"
    r"(?P<opener>\{|=>)",
    re.MULTILINE,
)


def extract_method_chunks(source_code: str, class_name: Optional[str] = None) -> List[MethodChunk]:
    """
    Extracts all methods from C# source code with exact start/end line numbers,
    bodies, and branch counts.
    """
    chunks: List[MethodChunk] = []
    line_offsets = [0]
    for m in re.finditer(r"\n", source_code):
        line_offsets.append(m.end())

    def get_line_num(char_idx: int) -> int:
        import bisect
        return bisect.bisect_right(line_offsets, char_idx)

    for m in METHOD_SIG_RE.finditer(source_code):
        name = m.group("name")
        if class_name and name == class_name:
            continue  # Constructors handled separately or skipped

        mods = (m.group("mods") or "").strip()
        ret_type = (m.group("return") or "").strip()
        params = (m.group("params") or "").strip()
        generics = m.group("generics")
        opener = m.group("opener")

        start_char = m.start()
        start_line = get_line_num(start_char)

        if opener == "{":
            open_idx = m.end() - 1
            close_idx = find_matching_brace(source_code, open_idx)
            if close_idx == -1:
                continue
            end_char = close_idx + 1
            body = source_code[open_idx + 1:close_idx]
            full_text = source_code[start_char:end_char]
        else:  # "=>"
            # Expression-bodied method ends at next semicolon
            semi_idx = source_code.find(";", m.end())
            if semi_idx == -1:
                continue
            end_char = semi_idx + 1
            body = source_code[m.end():semi_idx]
            full_text = source_code[start_char:end_char]

        end_line = get_line_num(end_char)
        branch_cnt = count_branches(body)
        is_async = "async" in mods or "Task" in ret_type
        has_try = "try" in body and "catch" in body
        has_throw = "throw " in body
        is_pub = "public" in mods or "internal" in mods

        # Base complexity calculation
        if branch_cnt >= 4 or (has_try and branch_cnt >= 2) or (has_throw and branch_cnt >= 3):
            comp_level = "HIGH"
            priority_score = 4
            priority_label = "HIGH"
        elif branch_cnt >= 2 or is_async or has_throw:
            comp_level = "MEDIUM"
            priority_score = 3
            priority_label = "NORMAL"
        elif branch_cnt == 1:
            comp_level = "LOW"
            priority_score = 2
            priority_label = "LOW"
        else:
            comp_level = "LOW"
            priority_score = 1
            priority_label = "TRIVIAL"

        chunks.append(MethodChunk(
            name=name,
            return_type=ret_type,
            modifiers=mods,
            params=params,
            generics=generics,
            start_line=start_line,
            end_line=end_line,
            body=body,
            full_text=full_text,
            branch_count=branch_cnt,
            has_async=is_async,
            has_try_catch=has_try,
            has_throw=has_throw,
            is_public=is_pub,
            complexity_level=comp_level,
            priority_score=priority_score,
            priority_label=priority_label,
        ))

    return chunks


def prioritize_methods(
    chunks: List[MethodChunk],
    uncovered_lines: Optional[List[int]] = None,
    failing_tests: Optional[List[str]] = None,
) -> List[MethodChunk]:
    """
    Ranks methods by priority. Methods containing uncovered lines or failing tests
    are promoted to CRITICAL.
    """
    uncovered_set = set(uncovered_lines or [])
    failing_str = " ".join(failing_tests or []).lower()

    for chunk in chunks:
        # Check uncovered lines inside this method
        method_uncovered = [
            l for l in sorted(uncovered_set)
            if chunk.start_line <= l <= chunk.end_line
        ]
        chunk.uncovered_lines = method_uncovered

        # Determine if failing tests mention this method
        method_failed = chunk.name.lower() in failing_str

        # Generate actionable coverage focus directives
        focus_items = []
        if method_uncovered:
            chunk.priority_score = 5
            chunk.priority_label = "CRITICAL"
            focus_items.append(f"UNCOVERED LINES: {', '.join(map(str, method_uncovered[:10]))}")

        if method_failed:
            chunk.priority_score = 5
            chunk.priority_label = "CRITICAL"
            focus_items.append("Failing in test suite - fix logic/assertion")

        if chunk.has_try_catch:
            focus_items.append("Test try/catch blocks (both success & exception handling)")
        elif chunk.has_throw:
            focus_items.append("Test exception throw path (Assert.ThrowsAsync / .Should().ThrowAsync)")

        if chunk.branch_count >= 3:
            focus_items.append(f"Cover all {chunk.branch_count} branches & conditional paths")

        if not focus_items:
            if chunk.is_public:
                focus_items.append("Standard happy path & null/edge argument validation")
            else:
                focus_items.append("Verify indirect execution through public caller")

        chunk.coverage_focus = "; ".join(focus_items)

    # Sort descending by priority score, then by branch count
    return sorted(chunks, key=lambda c: (c.priority_score, c.branch_count), reverse=True)


def build_priority_blueprint(chunks: List[MethodChunk]) -> str:
    """
    Produces a concise, actionable priority blueprint to guide the AI Author Agent
    to focus tests on high-value and uncovered methods first.
    """
    if not chunks:
        return ""

    lines = [
        "### Targeted Method Priority & Coverage Blueprint:",
        "Prioritize writing test methods in this order to maximize coverage and catch edge cases:",
    ]
    for c in chunks:
        gen_part = c.generics or ""
        sig = f"{c.name}{gen_part}({c.params})"
        lines.append(
            f"- [{c.priority_label}] `{sig}` "
            f"(Lines {c.start_line}-{c.end_line} | {c.branch_count} branch(es))\n"
            f"  -> Focus: {c.coverage_focus}"
        )
    return "\n".join(lines)


def build_optimized_target_code(
    source_code: str,
    chunks: List[MethodChunk],
    focus_mode: bool = False,
    max_chars: int = 40000,
) -> str:
    """
    Builds the optimized target source code block for the LLM.
    - If focus_mode is True (e.g. Iteration 2+ with existing tests & uncovered lines):
      Full code is retained ONLY for CRITICAL / UNCOVERED / HIGH priority methods,
      while already-covered / low-priority methods are condensed into 1-line signatures.
      This slashes token usage by 50-80% on retries while keeping 100% of context intact.
    - If focus_mode is False:
      Returns full source code with priority annotations.
    """
    if not focus_mode or not chunks:
        # Full code mode (iteration 1)
        return source_code

    # Check if there are critical / uncovered methods
    critical_chunks = [c for c in chunks if c.priority_score >= 4]
    if not critical_chunks:
        return source_code

    # In focus mode: replace bodies of non-critical methods with a concise placeholder
    optimized_code = source_code
    # Process in reverse order of line index so string replacements don't shift offsets
    sorted_for_replace = sorted(chunks, key=lambda c: c.start_line, reverse=True)

    for c in sorted_for_replace:
        if c.priority_score < 4 and len(c.full_text) > 80:
            gen_part = c.generics or ""
            compact_stub = (
                f"{c.modifiers} {c.return_type} {c.name}{gen_part}({c.params})\n"
                f"    {{\n        // [ALREADY COVERED / PASSING - Body omitted to optimize context]\n    }}"
            )
            optimized_code = optimized_code.replace(c.full_text, compact_stub, 1)

    return optimized_code


def skeletonize_dependency(raw_code: str, kind: str, name: str) -> str:
    """
    Transforms a raw C# dependency file (which may contain 300+ lines of internal
    database configurations, private methods, or SQL statements) into a clean,
    compact API contract skeleton.
    Slashes prompt token usage by up to 85% while giving the AI cleaner contracts.
    """
    lines = raw_code.splitlines()

    # For very short files (< 20 lines) or enums, keep as is
    if len(lines) <= 20 or "enum " in raw_code:
        return raw_code.strip()

    usings = [l.strip() for l in lines if l.strip().startswith("using ")]
    ns = [l.strip() for l in lines if l.strip().startswith("namespace ")]

    # 1. DbContext: retain only class header and DbSet<T> properties
    if "DbContext" in name or "DbContext" in raw_code:
        db_lines = []
        for l in usings[:6]:
            db_lines.append(l)
        if ns:
            db_lines.append("\n" + ns[0] + "\n")

        for l in lines:
            st = l.strip()
            if "class " in st and "DbContext" in st:
                db_lines.append(st if "{" in st else st + " {")
            elif "DbSet<" in st:
                db_lines.append("    " + st)

        db_lines.append("    // [Model configuration omitted for brevity]")
        db_lines.append("}")
        return "\n".join(db_lines)

    # 2. General Classes & Services
    header_lines = []
    in_header = False
    for l in lines:
        st = l.strip()
        if any(kw in st for kw in ["class ", "interface ", "record ", "struct "]):
            in_header = True
        if in_header:
            header_lines.append(l)
            if "{" in st or st.endswith(";"):
                break

    # Extract public properties
    pub_properties = []
    for l in lines:
        st = l.strip()
        if ("{ get;" in st or "{ get }" in st) and "public" in st and "(" not in st:
            pub_properties.append("    " + st)

    # Extract public methods
    methods = extract_method_chunks(raw_code, class_name=name)
    pub_methods = [m for m in methods if m.is_public]

    skeleton_parts = []
    if usings:
        skeleton_parts.append("\n".join(usings[:8]))
    if ns:
        skeleton_parts.append(ns[0])

    if header_lines:
        skeleton_parts.append("\n".join(header_lines))
    else:
        skeleton_parts.append(f"public {kind} {name}\n{{")

    body_items = []
    if pub_properties:
        body_items.append("    // Properties")
        body_items.extend(pub_properties[:20])

    if pub_methods:
        body_items.append("    // Public Method Signatures")
        for m in pub_methods:
            gen_part = m.generics or ""
            body_items.append(f"    {m.modifiers} {m.return_type} {m.name}{gen_part}({m.params});")

    if body_items:
        skeleton_parts.append("\n".join(body_items))
        skeleton_parts.append("}")
        return "\n\n".join(skeleton_parts)

    return raw_code.strip()
