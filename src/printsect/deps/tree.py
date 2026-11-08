"""The graph as a tree for people: outputs -> archives -> objects (sources) -> headers.

Also the answer to "what is this file built into" as lines, and every file the
graph knows, for searching.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from ..core.term import BOLD_YELLOW, CYAN, RESET, width
from .graph import ARCHIVE, HEADER, OBJECT, OUT, SOURCE, Graph
from .search import Matcher


@dataclass
class TreeOptions:
    sources: bool = False  # show the source file in place of its object
    headers: bool = False  # show every header (else only matching or probed ones)
    find: Matcher | None = None
    probed: bool = False  # keep only branches that hold a probe
    only: list[str] = field(default_factory=list)  # roots to show


@dataclass
class Node:
    path: str
    kind: str
    label: str
    probes: list[int] = field(default_factory=list)
    children: list[Node] = field(default_factory=list)
    hit: bool = False
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"path": self.path, "kind": self.kind}
        if self.source:
            data["source"] = self.source
        if self.probes:
            data["probes"] = self.probes
        if self.hit:
            data["match"] = True
        if self.children:
            data["children"] = [c.to_dict() for c in self.children]
        return data


def object_label(graph: Graph, obj: str, sources: bool = False) -> str:
    src: str | None = (graph.objects.get(obj) or {}).get("source")
    if sources and src:
        return src
    name = os.path.basename(obj)
    return f"{name} ← {src}" if src else obj


def build_tree(graph: Graph, probes: dict[str, list[int]], opts: TreeOptions) -> list[Node]:
    roots = graph.roots()
    if opts.only:
        wanted = [Matcher(name) for name in opts.only]
        roots = [r for r in roots if any(m(r) for m in wanted)]
    find = opts.find

    def node(path: str, ancestors: frozenset[str], inherited: bool) -> Node | None:
        kind = graph.kind(path) or OBJECT
        info = graph.objects.get(path) or {}
        source = info.get("source") if kind == OBJECT else None
        mine = list(probes.get(source, [])) if source else []
        if kind == OBJECT:
            label = object_label(graph, path, opts.sources)
        else:
            label = path
        hit = bool(find and (find(path) or find(source)))
        matched = inherited or find is None or hit
        kids: list[Node] = []
        below = ancestors | {path}
        for child in graph.children(path):
            if child not in below:
                n = node(child, below, matched)
                if n:
                    kids.append(n)
        for header in info.get("headers", []) if kind == OBJECT else []:
            h_probes = list(probes.get(header, []))
            h_hit = bool(find and find(header))
            if not (opts.headers or h_hit or h_probes):
                continue
            if (matched or h_hit) and (not opts.probed or h_probes):
                kids.append(Node(header, HEADER, header, h_probes, hit=h_hit))
        keep = matched and (not opts.probed or bool(mine))
        if kind != OBJECT and opts.probed:
            keep = False  # a container stays only for what is under it
        if not keep and not kids:
            return None
        return Node(path, kind, label, mine, kids, hit, source)

    out = []
    for r in roots:
        n = node(r, frozenset(), False)
        if n:
            out.append(n)
    return out


def render_tree(
    nodes: list[Node], *, ascii: bool = False, color: bool = False, find: Matcher | None = None
) -> list[str]:
    tee, last, pipe, blank = (
        ("|-- ", "`-- ", "|   ", "    ") if ascii else ("├── ", "└── ", "│   ", "    ")
    )
    rows: list[tuple[str, Node]] = []

    def walk(n: Node, prefix: str, child_prefix: str) -> None:
        rows.append((prefix, n))
        for i, c in enumerate(n.children):
            end = i == len(n.children) - 1
            walk(c, child_prefix + (last if end else tee), child_prefix + (blank if end else pipe))

    for n in nodes:
        walk(n, "", "")
    marked = [width(p + n.label) for p, n in rows if n.probes]
    column = min(max(marked, default=0), 72) + 2
    bullet = "*" if ascii else "●"
    lines = []
    for prefix, n in rows:
        label = n.label.replace("←", "<-") if ascii else n.label
        text = prefix + _highlight(label, find, color)
        if n.probes:
            pad = " " * max(column - width(prefix + label), 2)
            mark = f"{bullet} " + " ".join(f"#{p}" for p in n.probes)
            text += pad + (f"{CYAN}{mark}{RESET}" if color else mark)
        lines.append(text)
    return lines


def _highlight(label: str, find: Matcher | None, color: bool) -> str:
    if not (find and color):
        return label
    out, pos = [], 0
    for start, end in sorted(find.spans(label)):
        if start < pos:
            continue
        out += [label[pos:start], BOLD_YELLOW, label[start:end], RESET]
        pos = end
    out.append(label[pos:])
    return "".join(out)


def chain_text(graph: Graph, row: dict[str, Any], ascii: bool = False) -> str:
    arrow = " -> " if ascii else " → "
    first = row["chain"][0]
    head = object_label(graph, first) if graph.kind(first) == OBJECT else first
    if ascii:
        head = head.replace("←", "<-")
    return arrow.join([head, *row["chain"][1:]])


def render_which(
    graph: Graph, path: str, rows: list[dict[str, Any]], ascii: bool = False
) -> list[str]:
    tee, last = ("|-- ", "`-- ") if ascii else ("├── ", "└── ")
    texts = list(dict.fromkeys(chain_text(graph, r, ascii) for r in rows))
    return [path] + [(last if i == len(texts) - 1 else tee) + t for i, t in enumerate(texts)]


def all_files(graph: Graph) -> dict[str, str]:
    """Every path the graph knows, with its kind."""
    files: dict[str, str] = {}
    for h in graph.headers():
        files[h] = HEADER
    for s in graph.sources():
        files[s] = SOURCE
    for o in graph.objects:
        files[o] = OBJECT
    for a in graph.archives:
        files[a] = ARCHIVE
    for o in graph.outs:
        files[o] = OUT
    return files
