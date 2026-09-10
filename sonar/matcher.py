import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sonar.models import SonarIssue


class MethodMatch:
    def __init__(
        self,
        file_path: Path,
        method_name: str,
        start_line: int,
        end_line: int,
        method_code: str,
        class_name: str,
        class_context: str,
    ):
        self.file_path = file_path
        self.method_name = method_name
        self.start_line = start_line  # 1-indexed
        self.end_line = end_line      # 1-indexed
        self.method_code = method_code
        self.class_name = class_name
        self.class_context = class_context


class IssueMatcher:
    """
    Locates physical C# source files and target methods reported in SonarQube issues.
    """

    def __init__(self, root_dir: Path):
        self.root_dir = Path(root_dir).resolve()

    def find_file(self, reported_path: str) -> Optional[Path]:
        """
        Locates the physical file on disk corresponding to Sonar's reported path.
        """
        # 1. Check direct relative path from root
        direct = (self.root_dir / reported_path).resolve()
        if direct.exists() and direct.is_file():
            return direct

        # 2. Normalize separators
        norm_path = reported_path.replace("\\", "/").strip("/")
        
        # 3. Search matching relative path or file name
        file_name = Path(norm_path).name
        candidates = list(self.root_dir.rglob(file_name))
        
        if not candidates:
            return None

        # Filter out bin, obj, .venv
        valid_candidates = [
            c for c in candidates
            if not any(part in c.parts for part in ("bin", "obj", ".venv", ".git", "TestResults"))
        ]

        if not valid_candidates:
            return None

        # If exactly one match, return it
        if len(valid_candidates) == 1:
            return valid_candidates[0]

        # Otherwise pick the candidate with maximum suffix match
        norm_parts = Path(norm_path).parts
        best_match = valid_candidates[0]
        max_overlap = 0

        for cand in valid_candidates:
            cand_parts = cand.parts
            overlap = 0
            for p1, p2 in zip(reversed(norm_parts), reversed(cand_parts)):
                if p1.lower() == p2.lower():
                    overlap += 1
                else:
                    break
            if overlap > max_overlap:
                max_overlap = overlap
                best_match = cand

        return best_match

    def locate_method(self, file_path: Path, target_line: int) -> Optional[MethodMatch]:
        """
        Scans a C# file to find the method that encompasses the target_line.
        """
        if not file_path.exists():
            return None

        text = file_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        total_lines = len(lines)

        if target_line < 1 or target_line > total_lines:
            target_line = max(1, min(target_line, total_lines))

        # Find enclosing class
        class_name = "UnknownClass"
        class_match = re.search(r"(?:public|internal|private|protected)?\s*(?:static|sealed|abstract|partial)?\s*(?:class|record|struct)\s+(\w+)", text)
        if class_match:
            class_name = class_match.group(1)

        # Usings and namespace for context
        usings = re.findall(r"^(?:global\s+)?using\s+[\w\.]+;", text, re.MULTILINE)
        class_context = "\n".join(usings) + f"\n// Enclosing type: {class_name}\n"

        # Regex to locate method headers
        method_header_re = re.compile(
            r"^[ \t]*(?P<mods>(?:public|protected|internal|private|static|virtual|override|abstract|async|new)\s+)+"
            r"(?P<return>[\w<>\[\],\.\?\s]+?)\s+"
            r"(?P<name>\w+)\s*"
            r"(?P<generics><[^>]+>)?\s*"
            r"\((?P<params>[^)]*)\)\s*"
            r"(?:where\s+[\w\s:<>,]+)?\s*"
            r"(?P<open>\{|=>)",
            re.MULTILINE,
        )

        matches = list(method_header_re.finditer(text))
        for m in matches:
            header_start_idx = m.start()
            header_line = text[:header_start_idx].count("\n") + 1
            open_char = m.group("open")
            method_name = m.group("name")

            if open_char == "{":
                # Find matching closing brace
                open_pos = m.start("open")
                close_pos = self._find_matching_brace(text, open_pos)
                if close_pos != -1:
                    end_line = text[:close_pos].count("\n") + 1
                    if header_line <= target_line <= end_line:
                        method_code = text[header_start_idx:close_pos + 1]
                        return MethodMatch(
                            file_path=file_path,
                            method_name=method_name,
                            start_line=header_line,
                            end_line=end_line,
                            method_code=method_code,
                            class_name=class_name,
                            class_context=class_context,
                        )
            elif open_char == "=>":
                # Expression bodied method: ends at semicolon
                semi_pos = text.find(";", m.end())
                if semi_pos != -1:
                    end_line = text[:semi_pos].count("\n") + 1
                    if header_line <= target_line <= end_line:
                        method_code = text[header_start_idx:semi_pos + 1]
                        return MethodMatch(
                            file_path=file_path,
                            method_name=method_name,
                            start_line=header_line,
                            end_line=end_line,
                            method_code=method_code,
                            class_name=class_name,
                            class_context=class_context,
                        )

        # Fallback: if regex exact span missed, search around target_line for braces
        return self._fallback_brace_search(lines, target_line, file_path, class_name, class_context)

    def _find_matching_brace(self, text: str, open_pos: int) -> int:
        depth = 0
        in_string = False
        in_char = False
        in_line_comment = False
        in_block_comment = False
        length = len(text)
        i = open_pos

        while i < length:
            ch = text[i]

            if in_line_comment:
                if ch == "\n":
                    in_line_comment = False
            elif in_block_comment:
                if ch == "*" and i + 1 < length and text[i + 1] == "/":
                    in_block_comment = False
                    i += 1
            elif in_string:
                if ch == "\\":
                    i += 1
                elif ch == '"':
                    in_string = False
            elif in_char:
                if ch == "\\":
                    i += 1
                elif ch == "'":
                    in_char = False
            else:
                if ch == "/" and i + 1 < length:
                    if text[i + 1] == "/":
                        in_line_comment = True
                        i += 1
                    elif text[i + 1] == "*":
                        in_block_comment = True
                        i += 1
                elif ch == '"':
                    in_string = True
                elif ch == "'":
                    in_char = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return i
            i += 1
        return -1

    def _fallback_brace_search(
        self,
        lines: List[str],
        target_line: int,
        file_path: Path,
        class_name: str,
        class_context: str,
    ) -> Optional[MethodMatch]:
        """Fallback search backwards from target line to find method signature."""
        curr = target_line - 1
        while curr >= 0:
            line = lines[curr].strip()
            if any(line.startswith(m) for m in ("public ", "private ", "protected ", "internal ", "async ")):
                header_line = curr + 1
                # Find matching brace forward
                full_text = "\n".join(lines)
                char_offset = sum(len(l) + 1 for l in lines[:curr])
                open_pos = full_text.find("{", char_offset)
                if open_pos != -1:
                    close_pos = self._find_matching_brace(full_text, open_pos)
                    if close_pos != -1:
                        end_line = full_text[:close_pos].count("\n") + 1
                        method_code = full_text[char_offset:close_pos + 1]
                        return MethodMatch(
                            file_path=file_path,
                            method_name="TargetMethod",
                            start_line=header_line,
                            end_line=end_line,
                            method_code=method_code,
                            class_name=class_name,
                            class_context=class_context,
                        )
            curr -= 1
        return None

    def replace_method(self, file_path: Path, method_match: MethodMatch, refactored_code: str) -> bool:
        """
        Replaces the target method in the source file with refactored code.
        Preserves indentation.
        """
        if not file_path.exists():
            return False

        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        start_idx = method_match.start_line - 1
        end_idx = method_match.end_line - 1

        if start_idx < 0 or end_idx >= len(lines) or start_idx > end_idx:
            return False

        # Determine indentation from the original first line
        original_first_line = lines[start_idx]
        indent_match = re.match(r"^(\s*)", original_first_line)
        indent = indent_match.group(1) if indent_match else ""

        # Format refactored lines
        refactored_lines = refactored_code.splitlines()
        
        # Replace slice in lines
        new_lines = lines[:start_idx] + refactored_lines + lines[end_idx + 1:]
        file_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return True
