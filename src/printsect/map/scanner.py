"""What one C file defines at file scope, and which names it uses. No compiler needed.

It reads the shape of the file (comments, strings, braces, preprocessor lines), so it is
quick and needs no build, and it has the limits of that: `#if` is not evaluated (both
branches count, except `#if 0`), and a function made by a macro has no name to find.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

FUNCTION = "f"
VARIABLE = "v"

_ASCII = re.ASCII
KEYWORDS = frozenset("""
    auto break case char const continue default do double else enum extern float for goto
    if inline int long register restrict return short signed sizeof static struct switch
    typedef union unsigned void volatile while bool class namespace template typename
    using public private protected virtual operator new delete try catch throw
    __attribute__ __declspec __extension__ __inline __inline__ __asm__ __asm asm
""".split())

# Whatever is left open runs to where it must end (a comment to the end of the file, a
# string to the end of the line): matched once, so a file full of unclosed ones is not
# searched to the end from each.
_STRIP = re.compile(r"""//[^\n]*|/\*.*?(?:\*/|\Z)"""
                    r"""|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?""", re.S)
_TOKEN = re.compile(r"^[ \t]*\#(?:[^\n\\]|\\.)*|[{};]", re.M | re.S)
_DIRECTIVE = re.compile(r"\s*\#\s*(\w*)(.*)", re.S | _ASCII)
_DEFINE_HEAD = re.compile(r"\s*\#\s*define\s+\w+(?:\([^)]*\))?", _ASCII)
_DIRECTIVE_LINE = re.compile(r"^[ \t]*\#(?:[^\n\\]|\\.)*", re.M | re.S)
_ATTRIBUTE = re.compile(r"\b(?:__attribute__|__declspec)\s*\(\((?:[^()]|\([^()]*\))*\)\)"
                        r"|\b__declspec\s*\([^()]*\)", _ASCII)
_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b", _ASCII)
_PAREN = re.compile(r"[()]")
_LINKAGE = re.compile(r'(?:extern\s*""|(?:inline\s+)?namespace\b[^(]*)$', _ASCII)
_TYPE_WORD = re.compile(r"\b(?:struct|union|enum|class)\b", _ASCII)
_STATIC = re.compile(r"\bstatic\b", _ASCII)
_NOT_A_DEFINITION = re.compile(r"\b(?:typedef|extern|using|template|friend)\b", _ASCII)
_RETURNS_POINTER = re.compile(r"\(\s*\*\s*([A-Za-z_]\w*)\s*\(", _ASCII)
_POINTER_NAME = re.compile(r"\(\s*\*\s*(?:const\s+)?([A-Za-z_]\w*)\s*"
                           r"(?:\[[^\]\[]{0,256}\]\s*)*\)", _ASCII)
_BRACKETS = re.compile(r"\[[^\]\[]{0,256}\]")
_OLD_STYLE = re.compile(r"\b([A-Za-z_]\w*)\s*\(\s*[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*\s*\)\s+"
                        r"[A-Za-z_]", _ASCII)
# What stands between two file-scope boundaries, past which it is not read: no real
# declaration is this long, and the patterns above stay cheap on what is left.
MAX_SEGMENT = 64 << 10


@dataclass
class Scan:
    # (name, FUNCTION or VARIABLE, static, line)
    defs: list[tuple[str, str, bool, int]] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    macro_defs: int = 0  # bodies whose name a macro makes, so it cannot be read here
    unbalanced: bool = False  # braces did not close: what follows the fault is not seen
    too_long: int = 0  # declarations past MAX_SEGMENT, not read
    inside_if: int = 0  # definitions under an #if that cannot be told without a compiler


def _blank(match: re.Match[str]) -> str:
    text = match.group()
    newlines = "\n" * text.count("\n")
    if text[0] == "/":
        return newlines or " "
    return ('""' if text[0] == '"' else "0") + newlines


def _clean(raw: str) -> str:
    if "#" in raw:
        raw = _DIRECTIVE_LINE.sub(" ", raw)
    if "__" in raw:
        raw = _ATTRIBUTE.sub(" ", raw)
    return raw


def _word_before(text: str, end: int) -> str:
    """The identifier ending at `end`, past any spaces (no copy of what precedes it)."""
    while end and text[end - 1].isspace():
        end -= 1
    i = end
    while i and text[i - 1].isascii() and (text[i - 1].isalnum() or text[i - 1] == "_"):
        i -= 1
    return text[i:end]


def _called_names(head: str) -> list[str]:
    """The names right before each outermost parenthesis of a head, in order."""
    found = []
    level = 0
    for match in _PAREN.finditer(head):
        if match.group() == "(":
            if level == 0:
                word = _word_before(head, match.start())
                if word and not word[0].isdigit() and word not in KEYWORDS:
                    found.append(word)
            level += 1
        elif level:
            level -= 1
    return found


def _declarator_name(text: str) -> str | None:
    pointer = _POINTER_NAME.search(text)
    if pointer:
        return pointer.group(1)
    names = _IDENT.findall(_BRACKETS.sub(" ", text))
    if names and names[-1] not in KEYWORDS:
        return str(names[-1])
    return None


MAX_DECLARATORS = 256


def _split_top(text: str) -> list[str]:
    """Split on commas outside parentheses and brackets, up to MAX_DECLARATORS parts:
    finding each name's line is a search, and no real declaration gets that long."""
    parts: list[str] = []
    level = start = 0
    for i, ch in enumerate(text):
        if len(parts) >= MAX_DECLARATORS:
            return parts
        if ch in "([":
            level += 1
        elif ch in ")]":
            level -= 1
        elif ch == "," and level == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


class _Walk:
    def __init__(self, stripped: str) -> None:
        self.s = stripped
        self.out = Scan()
        self.line_at = 0
        self.line = 1
        self.old_style: tuple[str, bool, int] | None = None
        self.old_style_mark = 0
        self.in_if = False
        self.inside_if: set[str] = set()

    def _line_of(self, pos: int) -> int:
        if pos >= self.line_at:
            self.line += self.s.count("\n", self.line_at, pos)
        else:  # a name found earlier than the last one asked about
            self.line -= self.s.count("\n", pos, self.line_at)
        self.line_at = pos
        return self.line

    def _add(self, name: str, kind: str, static: bool, start: int, end: int) -> None:
        pos = self.s.rfind(name, start, end)
        if self.in_if:
            self.inside_if.add(name)
        self.out.defs.append((name, kind, static, self._line_of(pos if pos >= 0 else start)))

    def brace_at_top(self, start: int, end: int) -> str:
        """Read what a `{` at file scope opens: "open" (file scope goes on), "type",
        "typedef" or "body"."""
        if end - start > MAX_SEGMENT:
            self.out.too_long += 1
            self.old_style = None
            return "body"
        raw = self.s[start:end]
        head = _clean(raw)
        stripped = head.strip()
        old = self.old_style
        self.old_style = None
        if not stripped:
            if old:  # old-style: the parameters' declarations end right before the body
                del self.out.defs[self.old_style_mark:]
                self._add(old[0], FUNCTION, old[1], old[2], end)
            return "body"
        if _LINKAGE.match(stripped):
            return "open"
        equals = head.find("=")
        if equals >= 0 and head[:equals].count("(") == head[:equals].count(")"):
            lhs = head[:equals]
            if lhs.rstrip().endswith(")") and not _POINTER_NAME.search(lhs):
                self.out.macro_defs += 1  # NAME(x) = {...}: the variable is the macro's
                return "body"
            name = _declarator_name(lhs)
            if name and not _NOT_A_DEFINITION.search(lhs):
                self._add(name, VARIABLE, bool(_STATIC.search(lhs)), start, end)
            return "body"
        if "(" not in head:
            if _TYPE_WORD.search(stripped):
                return "typedef" if stripped.startswith("typedef") else "type"
            return "body"
        names = _called_names(head)
        if not names:
            pointer = _RETURNS_POINTER.search(head)
            if pointer:
                self._add(pointer.group(1), FUNCTION, bool(_STATIC.search(head)), start, end)
            return "body"
        lower = [n for n in names if not n.isupper()]
        if not lower and stripped.startswith(names[0]):
            self.out.macro_defs += 1
            return "body"
        name = lower[-1] if lower else names[-1]
        self._add(name.rsplit("::", 1)[-1], FUNCTION, bool(_STATIC.search(head)), start, end)
        return "body"

    def semicolon_at_top(self, start: int, end: int, after: str) -> None:
        if end - start > MAX_SEGMENT:
            self.out.too_long += 1
            return
        seg = _clean(self.s[start:end]).strip()
        if not seg:
            return
        if after in ("type", "typedef"):
            if after == "type":
                for part in _split_top(seg):
                    name = _declarator_name(part.split("=", 1)[0])
                    if name:
                        self._add(name, VARIABLE, bool(_STATIC.search(self._type_head)),
                                  start, end)
            return
        if _NOT_A_DEFINITION.search(seg):
            return
        equals = seg.find("=")
        lhs = seg[:equals] if equals >= 0 else seg
        if "(" in lhs:
            if lhs.count("(") == lhs.count(")"):
                pointer = _POINTER_NAME.search(lhs)
                if pointer:
                    self._add(pointer.group(1), VARIABLE, bool(_STATIC.search(lhs)), start, end)
                    return
            old = _OLD_STYLE.search(seg)
            if old and equals < 0:
                self.old_style = (old.group(1), bool(_STATIC.search(seg)), start)
                self.old_style_mark = len(self.out.defs)
            return
        parts = _split_top(seg)
        first = _IDENT.findall(_BRACKETS.sub(" ", parts[0].split("=", 1)[0]))
        if len(first) < 2 or (first[0] in ("struct", "union", "enum", "class") and len(first) == 2):
            return
        static = bool(_STATIC.search(seg))
        for part in parts:
            name = _declarator_name(part.split("=", 1)[0])
            if name:
                self._add(name, VARIABLE, static, start, end)

    _type_head = ""


def scan(text: str) -> Scan:
    """Read one C file's text."""
    return _read(text)[0]


def _in_code(s: str, cuts: list[tuple[int, int]],
             pattern: re.Pattern[str]) -> Iterator[tuple[str, int]]:
    """Each match of `pattern` in what a scan reads as code, with its line."""
    line = 1
    counted = at = 0
    for begin, end in [*cuts, (len(s), len(s))]:
        if begin > at:
            for match in pattern.finditer(s, at, begin):
                line += s.count("\n", counted, match.start())
                counted = match.start()
                yield match.group(), line
        at = max(at, end)


def use_lines(text: str, name: str) -> list[int]:
    """Each line where `name` appears in what a scan reads as code: not in a comment, a
    string or an `#if 0`, and so the same text the names of `scan` come from."""
    _, s, cuts = _read(text)
    lines: list[int] = []
    for _word, line in _in_code(s, cuts, re.compile(rf"\b{re.escape(name)}\b", _ASCII)):
        if not lines or lines[-1] != line:
            lines.append(line)
    return lines


def first_uses(text: str, names: set[str]) -> dict[str, int]:
    """The first line each of `names` appears on as code: one reading for all of them."""
    _, s, cuts = _read(text)
    found: dict[str, int] = {}
    for word, line in _in_code(s, cuts, _IDENT):
        if word in names and word not in found:
            found[word] = line
    return found


def _read(text: str) -> tuple[Scan, str, list[tuple[int, int]]]:
    """The scan, the text it read and the (begin, end) parts of it that are not code."""
    if "\r" in text:
        text = text.replace("\r\n", "\n")
    s = _STRIP.sub(_blank, text)
    walk = _Walk(s)
    depth = 0
    start = 0
    after = "body"
    # Each open #if: [depth at the #if, known value or None, this branch is dead].
    # Both sides of an #if may open a brace; each side starts from the depth at the #if.
    conditions: list[list[object]] = []
    dead = 0
    dead_from = 0
    cuts: list[tuple[int, int]] = []

    for match in _TOKEN.finditer(s):
        token = match.group()
        if token not in ("{", "}", ";"):
            directive = _DIRECTIVE.match(token)
            word = directive.group(1) if directive else ""
            rest = directive.group(2).strip() if directive else ""
            if not dead:
                body = match.end()
                if word == "define":
                    head = _DEFINE_HEAD.match(token)
                    body = match.start() + head.end() if head else match.end()
                cuts.append((match.start(), body))
            was_dead = dead
            if word in ("if", "ifdef", "ifndef"):
                value = {"0": 0, "(0)": 0, "1": 1, "(1)": 1}.get(rest) if word == "if" else None
                conditions.append([depth, value, value == 0])
                dead += value == 0
            elif word in ("else", "elif", "elifdef", "elifndef") and conditions:
                top = conditions[-1]
                if top[1] == 0 and top[2]:
                    top[2] = False
                    dead -= 1
                elif top[1] == 1 and not top[2]:
                    top[2] = True
                    dead += 1
                if not dead:
                    entered_at = int(top[0])  # type: ignore[call-overload]
                    if depth and not entered_at:
                        start = match.end()  # this branch writes its own head
                    depth = entered_at
            elif word == "endif" and conditions:
                if conditions.pop()[2]:
                    dead -= 1
            if dead and not was_dead:
                dead_from = match.end()
            elif was_dead and not dead:
                cuts.append((dead_from, match.end()))
                if depth == 0:
                    start = match.end()
            continue
        if dead:
            continue
        walk.in_if = any(c[1] is None for c in conditions)
        if token == "{":
            if depth == 0:
                walk._type_head = s[start:match.start()]
                after = walk.brace_at_top(start, match.start())
                if after == "open":
                    start = match.end()
                    continue
            depth += 1
        elif token == "}":
            if depth:
                depth -= 1
            if depth == 0:
                start = match.end()
        elif depth == 0:
            walk.semicolon_at_top(start, match.start(), after)
            start = match.end()
            after = "body"

    pieces = []
    at = 0
    for begin, end in cuts:
        if begin > at:
            pieces.append(s[at:begin])
        at = max(at, end)
    pieces.append(s[at:])
    first_of_each: dict[str, tuple[str, str, bool, int]] = {}
    for definition in walk.out.defs:  # `#ifdef` branches often define the same name
        first_of_each.setdefault(definition[0], definition)
    walk.out.defs = list(first_of_each.values())
    own = set(first_of_each)
    walk.out.refs = sorted(set(_IDENT.findall(" ".join(pieces))) - KEYWORDS - own)
    walk.out.unbalanced = depth != 0
    walk.out.inside_if = len(walk.inside_if & own)
    return walk.out, s, cuts
