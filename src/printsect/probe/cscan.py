"""A light look at C source: which function a line is in, and whether a line ends a
statement. Heuristics only - a code index adapter can answer more exactly."""

from __future__ import annotations

import re

KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof", "do", "else", "case"}
_IDENT_CALL = re.compile(r"([A-Za-z_]\w*)\s*\(")


def strip_code(lines: list[str]) -> list[str]:
    """Each line with comments, strings and char literals blanked out."""
    out: list[str] = []
    in_block = False
    for line in lines:
        buf: list[str] = []
        i = 0
        while i < len(line):
            ch = line[i]
            nxt = line[i + 1] if i + 1 < len(line) else ""
            if in_block:
                if ch == "*" and nxt == "/":
                    in_block = False
                    i += 2
                else:
                    i += 1
                continue
            if ch == "/" and nxt == "*":
                in_block = True
                i += 2
                continue
            if ch == "/" and nxt == "/":
                break
            if ch in "\"'":
                quote = ch
                i += 1
                while i < len(line) and line[i] != quote:
                    i += 2 if line[i] == "\\" else 1
                i += 1
                buf.append(" ")
                continue
            buf.append(ch)
            i += 1
        out.append("".join(buf))
    return out


def enclosing_function(lines: list[str], index: int) -> str | None:
    """The name of the function whose body holds line `index` (0-based), if any."""
    code = strip_code(lines[: index + 1])
    depth = 0
    header: list[str] = []
    current: str | None = None
    for text in code:
        if text.lstrip().startswith("#"):
            continue
        for ch in text:
            if ch == "{":
                if depth == 0:
                    current = _name_from_header("".join(header))
                depth += 1
                header = []
            elif ch == "}":
                depth = max(0, depth - 1)
                if depth == 0:
                    current = None
                header = []
            elif depth == 0:
                if ch == ";":
                    header = []
                else:
                    header.append(ch)
        if depth == 0:
            header.append(" ")
    return current if depth > 0 else None


def _name_from_header(header: str) -> str | None:
    names = [m for m in _IDENT_CALL.findall(header) if m not in KEYWORDS]
    if not names or "=" in header.split("(")[0]:
        return None  # struct/array initialiser, not a function
    return str(names[0])


def ends_statement(line: str) -> bool:
    """Whether a probe may go after this line without splitting a statement."""
    code = strip_code([line])[0].rstrip()
    stripped = line.strip()
    if not code.strip():
        return True  # blank or comment only
    if stripped.startswith("#"):
        return True
    return code.endswith((";", "{", "}", ":", "\\"))
