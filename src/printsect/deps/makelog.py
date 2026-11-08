"""Reading what a build would run (`make -n -B -w`) into a Graph.

The commands are told apart by their arguments, not by the tool's name, so any
compiler or cross toolchain works:

    compile   has `-c` and a source file        -> object (from -o, else the source's stem)
    archive   an archive file followed by objects, no -o   (ar rcs lib.a a.o b.o)
    link      -o and objects or archives as inputs        (any output name, ld -r too)

Folders come from make's "Entering directory" lines and from `cd` in a recipe line.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .graph import Graph
from .paths import join, to_rel

_DIR_RE = re.compile(
    r"^.*?make(?:\.exe)?(?:\[\d+\])?: (Entering|Leaving) directory [`'\"](.+?)['\"]\s*$"
)

# Options followed by their value as a separate word.
VALUE_OPTIONS = {
    "-o", "-I", "-isystem", "-iquote", "-idirafter", "-include", "-imacros", "-MF", "-MT",
    "-MQ", "-D", "-U", "-x", "-L", "-l", "-T", "-Xlinker", "-Xassembler", "-Xpreprocessor",
    "-u", "-e", "-z", "-arch", "--sysroot", "-isysroot", "-imultilib", "-iprefix",
}
REDIRECTS = {">", ">>", "<", "2>", "2>>", "&>", "1>"}


@dataclass
class Kinds:
    sources: tuple[str, ...] = (".c", ".cc", ".cpp", ".cxx", ".c++", ".s", ".asm")
    objects: tuple[str, ...] = (".o", ".obj")
    archives: tuple[str, ...] = (".a", ".lib")
    # Commands that never build anything, so their file arguments are not read.
    ignore_tools: frozenset[str] = frozenset(
        "rm cp mv ln echo printf mkdir rmdir touch cat test [ true false install chmod "
        "sed awk grep find tr sort head tail tee xargs date sleep make gmake".split()
    )

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> Kinds:
        def exts(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(str(e).lower() for e in settings.get(key, default))

        k = cls()
        return cls(
            sources=exts("source_extensions", k.sources),
            objects=exts("object_extensions", k.objects),
            archives=exts("archive_extensions", k.archives),
            ignore_tools=frozenset(settings.get("ignore_tools", k.ignore_tools))
            | frozenset(settings.get("also_ignore", [])),
        )

    def has(self, path: str, exts: tuple[str, ...]) -> bool:
        return os.path.splitext(path)[1].lower() in exts


# -- words ---------------------------------------------------------------------------


def logical_lines(text: str) -> Iterable[str]:
    """Lines with `\\` + newline continuations joined."""
    buf = ""
    for raw in text.splitlines():
        if raw.endswith("\\") and not raw.endswith("\\\\"):
            buf += raw[:-1] + " "
            continue
        yield buf + raw
        buf = ""
    if buf:
        yield buf


def split_commands(line: str) -> list[list[str]]:
    """Shell-like split into commands (at && || ; | ( )) and words.

    A backslash only escapes a quote, a space or another backslash, so Windows paths
    such as `src\\net\\rx.c` keep their separators.
    """
    commands: list[list[str]] = []
    words: list[str] = []
    word: list[str] = []
    started = False
    quote: str | None = None
    i = 0

    def end_word() -> None:
        nonlocal word, started
        if started:
            words.append("".join(word))
        word, started = [], False

    def end_command() -> None:
        nonlocal words
        end_word()
        if words:
            commands.append(words)
        words = []

    while i < len(line):
        ch = line[i]
        nxt = line[i + 1] if i + 1 < len(line) else ""
        if quote:
            if ch == "\\" and quote == '"' and nxt in '"\\':
                word.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
            else:
                word.append(ch)
        elif ch == "\\" and nxt and nxt in "\"' \\":
            word.append(nxt)
            started = True
            i += 2
            continue
        elif ch in "'\"":
            quote = ch
            started = True
        elif ch.isspace():
            end_word()
        elif line.startswith(("&&", "||"), i):
            end_command()
            i += 2
            continue
        elif ch in ";|()":
            end_command()
        else:
            word.append(ch)
            started = True
        i += 1
    end_command()
    return commands


# -- reading one command -----------------------------------------------------------------


@dataclass
class Parsed:
    tool: str
    positional: list[str] = field(default_factory=list)
    options: dict[str, list[str]] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)


def parse_args(words: list[str], cwd: str) -> Parsed:
    tool = os.path.basename(words[0]).lower()
    if tool.endswith(".exe"):
        tool = tool[:-4]
    parsed = Parsed(tool=tool)
    args = _expand_response_files(words[1:], cwd)
    i = 0
    while i < len(args):
        a = args[i]
        if a in REDIRECTS:
            i += 2
            continue
        if a[:1] in "<>" or a.startswith(("2>", "1>")):
            i += 1
            continue
        if a in VALUE_OPTIONS and i + 1 < len(args):
            parsed.options.setdefault(a, []).append(args[i + 1])
            i += 2
            continue
        if a.startswith("-") and len(a) > 1:
            for opt in ("-isystem", "-iquote", "-idirafter", "-I", "-L", "-l", "-o"):
                if a.startswith(opt) and len(a) > len(opt) and not a.startswith("-include"):
                    parsed.options.setdefault(opt, []).append(a[len(opt):])
                    break
            else:
                parsed.flags.add(a)
            i += 1
            continue
        parsed.positional.append(a)
        i += 1
    return parsed


def _expand_response_files(args: list[str], cwd: str) -> list[str]:
    out: list[str] = []
    for a in args:
        if a.startswith("@") and len(a) > 1:
            path = join(cwd, a[1:])
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    for command in split_commands(handle.read().replace("\n", " ")):
                        out.extend(command)
                continue
            except OSError:
                pass
        out.append(a)
    return out


# -- building the graph ------------------------------------------------------------------


class Reader:
    def __init__(self, root: str, cwd: str, kinds: Kinds) -> None:
        self.root = root
        self.start = cwd
        self.kinds = kinds
        self.graph = Graph()
        self._links: dict[str, dict[str, Any]] = {}
        self.counts = {"compile": 0, "archive": 0, "link": 0}

    def rel(self, cwd: str, path: str) -> str:
        return to_rel(self.root, join(cwd, path))

    def feed(self, text: str) -> None:
        dirs = [self.start]
        for line in logical_lines(text):
            m = _DIR_RE.match(line.strip())
            if m:
                if m.group(1) == "Entering":
                    dirs.append(join(dirs[-1], m.group(2)))
                elif len(dirs) > 1:
                    dirs.pop()
                continue
            cwd = dirs[-1]
            for words in split_commands(line):
                words = _drop_assignments(words)
                if not words:
                    continue
                if words[0] == "cd":
                    cwd = join(cwd, words[1]) if len(words) > 1 else cwd
                    continue
                self.command(words, cwd)

    def command(self, words: list[str], cwd: str) -> None:
        k = self.kinds
        p = parse_args(words, cwd)
        if p.tool in k.ignore_tools:
            return
        out = p.options.get("-o", [None])[-1]
        if "-c" in p.flags:
            sources = [s for s in p.positional if k.has(s, k.sources)]
            if sources:
                self._compile(p, cwd, sources, out)
            return
        if out is None:
            self._archive(p, cwd)
            return
        inputs = [
            self.rel(cwd, a)
            for a in p.positional
            if k.has(a, k.objects) or k.has(a, k.archives) or self.rel(cwd, a) in self._links
        ]
        if inputs or p.options.get("-l"):
            self._link(p, cwd, self.rel(cwd, out), inputs)

    def _compile(self, p: Parsed, cwd: str, sources: list[str], out: str | None) -> None:
        include = [self.rel(cwd, d) for opt in ("-I", "-isystem", "-idirafter")
                   for d in p.options.get(opt, []) if d != "-"]
        quote = [self.rel(cwd, d) for d in p.options.get("-iquote", [])]
        forced = [self.rel(cwd, f) for f in p.options.get("-include", [])]
        for src in sources:
            if out and len(sources) == 1:
                obj = self.rel(cwd, out)
            else:
                stem = os.path.splitext(os.path.basename(src))[0]
                obj = self.rel(cwd, stem + self.kinds.objects[0])
            self.graph.objects[obj] = {
                "source": self.rel(cwd, src),
                "include_dirs": include,
                "quote_dirs": quote,
                "forced": forced,
                "headers": [],
            }
            self.counts["compile"] += 1

    def _archive(self, p: Parsed, cwd: str) -> None:
        k = self.kinds
        args = p.positional
        if args and re.fullmatch(r"-?[A-Za-z]+", args[0]) and not k.has(args[0], k.archives):
            op = args[0].lstrip("-")
            if not set(op) & set("rq"):  # d (delete), t (list), x (extract), ...
                return
            args = args[1:]
        if not args or not k.has(args[0], k.archives):
            return
        members = [self.rel(cwd, a) for a in args[1:]
                   if k.has(a, k.objects) or self.rel(cwd, a) in self._links]
        if not members:
            return
        archive = self.rel(cwd, args[0])
        # made_in: where the build made it (e.g. the folder of a sub-make), for rules
        # such as `make -C {made_in}`.
        entry = self.graph.archives.setdefault(
            archive, {"objects": [], "made_in": to_rel(self.root, cwd)})
        have = entry["objects"]
        for m in members:  # `ar r` adds to an archive; several calls build one up
            if m not in have:
                have.append(m)
        self.counts["archive"] += 1

    def _link(self, p: Parsed, cwd: str, out: str, inputs: list[str]) -> None:
        lib_dirs = [join(cwd, d) for d in p.options.get("-L", [])]
        self._links[out] = {"inputs": inputs, "libs": p.options.get("-l", []),
                            "lib_dirs": lib_dirs, "made_in": to_rel(self.root, cwd)}
        self.counts["link"] += 1

    def finish(self) -> Graph:
        g = self.graph
        k = self.kinds
        by_name: dict[str, list[str]] = {}
        for a in g.archives:
            by_name.setdefault(os.path.basename(a), []).append(a)
        for out, link in self._links.items():
            objects, archives, missing = [], [], []
            for item in link["inputs"]:
                if item in self._links or item in g.objects or k.has(item, k.objects):
                    objects.append(item)
                else:
                    archives.append(item)
            for name in link["libs"]:
                found = self._find_lib(name, link["lib_dirs"], by_name)
                if found:
                    archives.append(found)
                else:
                    missing.append(name)
            g.outs[out] = {"objects": _unique(objects), "archives": _unique(archives),
                           "libs": missing, "made_in": link["made_in"]}
        for a in [a for o in g.outs.values() for a in o["archives"]]:
            if a not in g.archives:
                g.archives[a] = {"objects": [], "prebuilt": True}
        return g

    def _find_lib(self, name: str, dirs: list[str], by_name: dict[str, list[str]]) -> str | None:
        if name.startswith(":"):  # -l:exact.a
            files = [name[1:]]
        else:
            files = [f"lib{name}{ext}" for ext in self.kinds.archives] + [f"{name}.lib"]
        for d in dirs:
            for f in files:
                rel = to_rel(self.root, os.path.join(d, f))
                if rel in self.graph.archives:
                    return rel
        for f in files:
            if len(by_name.get(f, [])) == 1:
                return by_name[f][0]
        return None


def _drop_assignments(words: list[str]) -> list[str]:
    i = 0
    while i < len(words) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[i]):
        i += 1
    if words[i:i + 1] == ["env"]:
        return _drop_assignments(words[i + 1:])
    return words[i:]


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def read_log(
    text: str, root: str, cwd: str, kinds: Kinds | None = None
) -> tuple[Graph, dict[str, int]]:
    reader = Reader(root, cwd, kinds or Kinds())
    reader.feed(text)
    return reader.finish(), reader.counts
