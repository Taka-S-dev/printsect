"""Which headers each object is built from, by following `#include` as the compiler would.

The search order is the usual one: for `"x.h"` the including file's folder, then
-iquote, then -I/-isystem; for `<x.h>` only -I/-isystem. Only headers inside the
project are kept and followed. `#if` is not evaluated, so a header behind a branch
that is never taken still counts: the answer can be a little too wide, never too narrow.

Each file is read once, and where an include leads is worked out once for each set of
search folders, so a large project (thousands of sources sharing headers) stays fast.
"""

from __future__ import annotations

import os
import re

from .graph import Graph
from .paths import relativizer, to_abs

_INCLUDE_RE = re.compile(rb'^[ \t]*#[ \t]*include[ \t]*([<"])([^>"\r\n]+)[>"]', re.M)


def _read_includes(path: str) -> list[tuple[str, str]]:
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return []
    return [
        (m.group(1).decode(), m.group(2).decode("latin-1").strip())
        for m in _INCLUDE_RE.finditer(data)
    ]


def trace(graph: Graph, root: str) -> None:
    rel, inside = relativizer(root)
    includes: dict[str, list[tuple[str, str]]] = {}
    exists: dict[str, bool] = {}
    # (file, search folders) -> the project headers its includes lead to
    leads: dict[tuple[str, tuple[str, ...], tuple[str, ...]], list[str]] = {}

    def is_file(path: str) -> bool:
        found = exists.get(path)
        if found is None:
            found = exists[path] = os.path.isfile(path)
        return found

    def resolve(current: str, quote: tuple[str, ...], dirs: tuple[str, ...]) -> list[str]:
        key = (current, quote, dirs)
        found = leads.get(key)
        if found is not None:
            return found
        if current not in includes:
            includes[current] = _read_includes(current)
        found = []
        here = os.path.dirname(current)
        for kind, name in includes[current]:
            where = ((here, *quote) if kind == '"' else ()) + dirs
            for folder in where:
                path = os.path.normpath(os.path.join(folder, name))
                if is_file(path):
                    if inside(path):  # outside the project: neither kept nor followed
                        found.append(path)
                    break
        leads[key] = found
        return found

    for info in graph.objects.values():
        source = info.get("source")
        if not source:
            continue
        dirs = tuple(to_abs(root, d) for d in info.get("include_dirs", []))
        quote = tuple(to_abs(root, d) for d in info.get("quote_dirs", []))
        seen: set[str] = set()
        stack = [to_abs(root, source)]
        for forced in info.get("forced", []):
            path = to_abs(root, forced)
            if is_file(path) and inside(path):
                seen.add(path)
                stack.append(path)
        while stack:
            for path in resolve(stack.pop(), quote, dirs):
                if path not in seen:
                    seen.add(path)
                    stack.append(path)
        info["headers"] = sorted(rel(p) for p in seen)
    graph.invalidate()
