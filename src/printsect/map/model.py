"""From an index to the map: which module uses which, and how much.

What is counted is (symbol, file using it) pairs, not lines: a file calling a function
ten times counts once, which is steadier as a measure of a boundary. Only functions and
variables defined in source files count, and only names used in source files: headers
are mostly declarations, and everything includes the public ones.

Names are matched without a compiler, so nothing is guessed. A name with several
definitions the using file cannot be told to mean is left out, and how many were left
out is always part of the answer: not on the map does not mean not there.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .cycles import loops, round_trip
from .index import Index

ROOT_FOLDER = "./"
SPLIT_SHARE = 0.15
MAX_GROUPS = 40
SYMBOLS_SHOWN = 5


@dataclass
class Tables:
    edges: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # (user, definer)
    lines: dict[tuple[str, str], int] = field(default_factory=dict)  # (file, name) -> line
    files: list[str] = field(default_factory=list)
    omitted: dict[str, int] = field(default_factory=dict)


def build(index: Index, in_build: Iterable[str] | None = None) -> Tables:
    """The file-to-file edges. `in_build` keeps only those files, when the build is known:
    other files (another platform's, for one) cannot be what a built file means."""
    scope = set(in_build) if in_build is not None else None
    files = {f: e for f, e in index.files.items() if scope is None or f in scope}
    definers: dict[str, list[str]] = defaultdict(list)
    statics: set[str] = set()
    tables = Tables(files=sorted(files))
    for name, entry in files.items():
        for symbol, _kind, static, line in entry.defs:
            tables.lines.setdefault((name, symbol), line)
            if static:
                statics.add(symbol)
            elif name not in definers[symbol]:
                definers[symbol].append(name)
    same_name = {s for s, where in definers.items() if len(where) > 1}
    same_name_refs = static_refs = 0
    edges: dict[tuple[str, str], list[str]] = defaultdict(list)
    for name, entry in files.items():
        for symbol in entry.refs:  # never a name the file defines itself
            where = definers.get(symbol)
            if not where:
                # Only static definitions: no other file can mean those. No definition
                # at all: from outside the tree, such as libc.
                static_refs += symbol in statics
            elif len(where) > 1:
                same_name_refs += 1
            else:
                edges[(name, where[0])].append(symbol)
    tables.edges = dict(edges)
    tables.omitted = {
        "same_name_symbols": len(same_name), "same_name_refs": same_name_refs,
        "static_refs": static_refs,
        "macro_made_definitions": sum(e.macro_defs for e in files.values()),
        "unbalanced_files": sum(e.unbalanced for e in files.values()),
        "too_long_declarations": sum(e.too_long for e in files.values()),
        "too_large": index.too_large, "unreadable": index.unreadable, "links": index.links,
        "not_in_build": len(index.files) - len(files),
    }
    return tables


def _folder_at(file: str, depth: int) -> str:
    """The file's folder, cut to `depth` levels; "./" for a file in the project root."""
    parts = file.split("/")[:-1]
    return "/".join(parts[:depth]) + "/" if parts else ROOT_FOLDER


def _lookup(groups: set[str], file: str) -> str:
    at = file.rfind("/")
    while at >= 0:
        prefix = file[:at + 1]
        if prefix in groups:
            return prefix
        at = file.rfind("/", 0, at)
    return ROOT_FOLDER


def directory_groups(tables: Tables) -> dict[str, str]:
    """Each file's module: start from the top folders and split the heaviest, so a big
    folder is shown by its parts (`drivers/net/` beside `net/`) and a small one stays whole.
    A folder that gave up a part stands for what remains."""
    weights: Counter[str] = Counter()
    for (user, definer), symbols in tables.edges.items():
        weights[user] += len(symbols)
        weights[definer] += len(symbols)
    groups = {_folder_at(f, 1) for f in tables.files}
    total = sum(weights.values())
    settled: set[str] = set()
    while total and len(groups) < MAX_GROUPS:
        by_group: Counter[str] = Counter()
        for file, weight in weights.items():
            by_group[_lookup(groups, file)] += weight
        heavy = sorted((g for g, w in by_group.items()
                        if g not in settled and w > SPLIT_SHARE * total),
                       key=lambda g: (-by_group[g], g))
        if not heavy:
            break
        best = heavy[0]
        children: Counter[str] = Counter()
        depth = best.count("/") + 1
        for file, weight in weights.items():
            if _lookup(groups, file) == best:
                child = _folder_at(file, depth)
                if child != best:
                    children[child] += weight
        if not children:
            settled.add(best)
            continue
        groups.add(sorted(children, key=lambda c: (-children[c], c))[0])
    return {f: _lookup(groups, f) for f in tables.files}


def _role(users: int, used: int) -> str:
    """Where a part sits: `start` uses others and nothing uses it, `base` is only used,
    `core` is used by three or more and uses others."""
    if not users and not used:
        return "isolated"
    if not users:
        return "start"
    if not used:
        return "base"
    return "core" if users >= 3 else "middle"


def _capped(rows: list[Any], top: int) -> dict[str, Any]:
    return {"shown": rows[:top], "total": len(rows)}


class Map:
    """The answers: the whole picture, and one module seen from inside."""

    def __init__(self, tables: Tables, modules: dict[str, str] | None = None,
                 marks: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.t = tables
        self.grouped_by = "archive" if modules else "directory"
        self.owner = modules if modules else directory_groups(tables)
        self.marks = marks or {}

    def _module_of(self, file: str) -> str:
        return self.owner.get(file, "(not in an archive)")

    def overview(self, top: int) -> dict[str, Any]:
        between: Counter[tuple[str, str]] = Counter()
        for (user, definer), symbols in self.t.edges.items():
            a, b = self._module_of(user), self._module_of(definer)
            if a != b:
                between[(a, b)] += len(symbols)
        files = Counter(self._module_of(f) for f in self.t.files)
        probes: Counter[str] = Counter()
        for file, marks in self.marks.items():
            if file in self.owner:
                probes[self._module_of(file)] += len(marks)
        count_in: Counter[str] = Counter()
        count_out: Counter[str] = Counter()
        users: dict[str, set[str]] = defaultdict(set)
        used: dict[str, set[str]] = defaultdict(set)
        for (a, b), n in between.items():
            count_out[a] += n
            count_in[b] += n
            users[b].add(a)
            used[a].add(b)
        rows: list[dict[str, Any]] = [
                {"name": m, "files": n, "role": _role(len(users[m]), len(used[m])),
                 "in": count_in[m], "out": count_out[m],
                 "used_by": len(users[m]), "uses": len(used[m]), "probes": probes[m]}
                for m, n in files.items()]
        rows.sort(key=lambda r: (-count_in[r["name"]], -count_out[r["name"]], r["name"]))
        edges = [{"from": a, "to": b, "count": n}
                 for (a, b), n in sorted(between.items(), key=lambda kv: (-kv[1], kv[0]))]
        return {"files": len(self.t.files), "grouped_by": self.grouped_by,
                "modules": _capped(rows, top), "edges": _capped(edges, top),
                "cycles": _capped(self._cycles(between, top), top),
                "omitted": self.t.omitted}

    def _cycles(self, between: Counter[tuple[str, str]], top: int) -> list[dict[str, Any]]:
        """Each loop among the modules, with one shortest way round and its symbols."""
        uses: dict[str, list[str]] = defaultdict(list)
        for (a, b), _n in sorted(between.items(), key=lambda kv: (-kv[1], kv[0])):
            uses[a].append(b)
        groups = loops(uses)
        paths = []
        for group in groups:
            members = set(group)
            weight: Counter[str] = Counter()
            for (a, b), n in between.items():
                if a in members and b in members:
                    weight[a] += n
                    weight[b] += n
            start = min(group, key=lambda m: (-weight[m], m))
            path = round_trip(start, uses, members)
            paths.append(list(zip(path, [*path[1:], path[0]], strict=True)))
        wanted = {leg for path in paths for leg in path}
        symbols: dict[tuple[str, str], Counter[tuple[str, str]]] = defaultdict(Counter)
        for (user, definer), names in self.t.edges.items():
            leg = (self._module_of(user), self._module_of(definer))
            if leg in wanted:
                symbols[leg].update((definer, s) for s in names)
        rows: list[dict[str, Any]] = [
            {"modules": _capped(group, top), "round_trip": [
                {"from": a, "to": b, "count": between[(a, b)], **self._symbols(symbols[(a, b)])}
                for a, b in path]}
            for group, path in zip(groups, paths, strict=True)]
        rows.sort(key=lambda r: (-r["modules"]["total"], r["modules"]["shown"][0]))
        return rows

    def known_names(self) -> list[str]:
        names = set(self.owner.values())
        for file in self.t.files:
            at = file.find("/")
            while at >= 0:
                names.add(file[:at + 1])
                at = file.find("/", at + 1)
        return sorted(names)

    def resolve(self, name: str) -> str | None:
        """A module, archive or folder, however it was typed."""
        text = name.replace("\\", "/").removeprefix("./")
        names = self.known_names()
        if not text.rstrip(".") and ROOT_FOLDER in names:
            return ROOT_FOLDER
        for candidate in (text, text.rstrip("/") + "/"):
            if candidate in names:
                return candidate
        ends = [n for n in names if n.rstrip("/").rsplit("/", 1)[-1] == text.rstrip("/")]
        return ends[0] if len(ends) == 1 else None

    def _inside(self, module: str, file: str) -> bool:
        return self._module_of(file) == module or (module.endswith("/") and file.startswith(module))

    def _symbols(self, pairs: Counter[tuple[str, str]]) -> dict[str, Any]:
        """The most used names of an edge, each with where it is defined."""
        ordered = sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))
        shown = []
        for (file, symbol), _n in ordered[:SYMBOLS_SHOWN]:
            shown.append({"name": symbol, "file": file, "line": self.t.lines.get((file, symbol))})
        return {"symbols": shown, "more_symbols": max(0, len(ordered) - SYMBOLS_SHOWN)}

    def focus(self, module: str, top: int) -> dict[str, Any]:
        inside_files = [f for f in self.t.files if self._inside(module, f)]
        members = set(inside_files)
        came_in: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
        went_out: dict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
        within: dict[tuple[str, str], Counter[tuple[str, str]]] = defaultdict(Counter)
        outside_in: Counter[str] = Counter()
        outside_out: Counter[str] = Counter()
        users: dict[str, set[str]] = defaultdict(set)
        used: dict[str, set[str]] = defaultdict(set)
        for (user, definer), symbols in self.t.edges.items():
            pairs = Counter((definer, s) for s in symbols)
            if user in members and definer in members:
                within[(user, definer)].update(pairs)
                users[definer].add(user)
                used[user].add(definer)
            elif definer in members:
                came_in[self._module_of(user)].update(pairs)
                outside_in[definer] += len(symbols)
            elif user in members:
                went_out[self._module_of(definer)].update(pairs)
                outside_out[user] += len(symbols)

        def side(table: dict[str, Counter[tuple[str, str]]]) -> list[dict[str, Any]]:
            rows = [{"module": m, "count": sum(c.values()), **self._symbols(c)}
                    for m, c in table.items()]
            rows.sort(key=lambda r: (-r["count"], r["module"]))
            return rows

        inner = [{"from": a, "to": b, "count": sum(c.values()), **self._symbols(c)}
                 for (a, b), c in within.items()]
        inner.sort(key=lambda r: (-r["count"], r["from"], r["to"]))
        files: list[dict[str, Any]] = [
                 {"file": f, "role": _role(len(users[f]), len(used[f])),
                  "from_outside": outside_in[f], "to_outside": outside_out[f],
                  "probes": [m.get("n") for m in self.marks.get(f, [])]}
                 for f in inside_files]
        files.sort(key=lambda r: (-outside_in[r["file"]], r["file"]))
        return {"module": module, "files_in_module": len(inside_files),
                "grouped_by": self.grouped_by,
                "from_outside": _capped(side(came_in), top),
                "to_outside": _capped(side(went_out), top),
                "inside": _capped(inner, top), "files": _capped(files, top),
                "omitted": self.t.omitted}
