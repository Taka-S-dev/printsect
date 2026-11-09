"""What is built from what: sources and headers -> objects -> archives -> outputs.

Every path is relative to the project root with forward slashes, or absolute when it is
outside the root. `outs` holds everything a link step makes, which includes partially
linked objects (`ld -r -o big.o ...`); such an object is a container like an archive.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GRAPH_VERSION = 1
IMPORTED = "(a graph file)"  # Graph.command when it came from --from-graph, not a command

OUT = "out"
ARCHIVE = "archive"
OBJECT = "object"
SOURCE = "source"
HEADER = "header"


@dataclass
class Graph:
    # object -> {"source": str | None, "include_dirs": [...], "quote_dirs": [...],
    #            "forced": [...], "headers": [...]}
    objects: dict[str, dict[str, Any]] = field(default_factory=dict)
    # archive -> {"objects": [...]}
    archives: dict[str, dict[str, Any]] = field(default_factory=dict)
    # output -> {"objects": [...], "archives": [...], "libs": [names not found]}
    outs: dict[str, dict[str, Any]] = field(default_factory=dict)
    generated: str = ""
    command: str = ""
    inputs: dict[str, int] = field(default_factory=dict)  # watched file -> mtime_ns
    warnings: list[str] = field(default_factory=list)
    headers_traced: bool = True  # False: deps.headers is off, objects list no headers
    _cache: dict[str, Any] = field(default_factory=dict, init=False, repr=False, compare=False)

    # -- storage -------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": GRAPH_VERSION,
            "generated": self.generated,
            "command": self.command,
            "outs": self.outs,
            "archives": self.archives,
            "objects": self.objects,
            "inputs": self.inputs,
            "warnings": self.warnings,
            "headers_traced": self.headers_traced,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Graph:
        return cls(
            objects=dict(data.get("objects") or {}),
            archives=dict(data.get("archives") or {}),
            outs=dict(data.get("outs") or {}),
            generated=str(data.get("generated") or ""),
            command=str(data.get("command") or ""),
            inputs={k: int(v) for k, v in (data.get("inputs") or {}).items()},
            warnings=list(data.get("warnings") or []),
            headers_traced=bool(data.get("headers_traced", True)),
        )

    # -- shape ---------------------------------------------------------------------

    def kind(self, path: str) -> str | None:
        if path in self.outs:
            return OUT
        if path in self.archives:
            return ARCHIVE
        if path in self.objects:
            return OBJECT
        return None

    def children(self, path: str) -> list[str]:
        """What a container is made of, in the order the build gave them."""
        if path in self.outs:
            info = self.outs[path]
            return list(info.get("objects", [])) + list(info.get("archives", []))
        if path in self.archives:
            return list(self.archives[path].get("objects", []))
        return []

    # -- indexes (built once, on first use; call invalidate() after changing the graph) --

    def invalidate(self) -> None:
        self._cache.clear()

    def parents(self) -> dict[str, list[str]]:
        """Container -> the containers it is directly part of."""
        if "parents" not in self._cache:
            up: dict[str, list[str]] = {}
            for container in [*self.outs, *self.archives]:
                for child in self.children(container):
                    have = up.setdefault(child, [])
                    if container not in have:
                        have.append(container)
            self._cache["parents"] = up
        parents: dict[str, list[str]] = self._cache["parents"]
        return parents

    def users(self) -> dict[str, list[tuple[str, str]]]:
        """Source or header -> the objects built from it, with "source" or "header"."""
        if "users" not in self._cache:
            index: dict[str, list[tuple[str, str]]] = {}
            for obj, info in self.objects.items():
                if info.get("source"):
                    index.setdefault(info["source"], []).append((obj, SOURCE))
                for h in info.get("headers", []):
                    index.setdefault(h, []).append((obj, HEADER))
            self._cache["users"] = index
        users: dict[str, list[tuple[str, str]]] = self._cache["users"]
        return users

    def roots(self) -> list[str]:
        """Outputs and archives that nothing else is built from."""
        used = self.parents()
        tops = [p for p in self.outs if p not in used]
        tops += [p for p in self.archives if p not in used]
        return sorted(tops)

    def final_outs(self) -> list[str]:
        """Link outputs that are not linked into anything else (not `ld -r` parts)."""
        return [r for r in self.roots() if r in self.outs]

    def sources(self) -> set[str]:
        return {o["source"] for o in self.objects.values() if o.get("source")}

    def headers(self) -> set[str]:
        return {h for o in self.objects.values() for h in o.get("headers", [])}

    # -- questions -----------------------------------------------------------------

    def starts_for(self, path: str) -> list[tuple[str, str]]:
        """The graph nodes a file stands for, with how: (node, "source"|"header"|"self")."""
        found: list[tuple[str, str]] = [(path, "self")] if self.kind(path) else []
        return found + list(self.users().get(path, []))

    def roots_of(self, node: str) -> frozenset[str]:
        """The roots `node` ends up in. Remembered, so asking for many files stays cheap."""
        memo: dict[str, frozenset[str]] = self._cache.setdefault("roots_of", {})
        if node in memo:
            return memo[node]
        up = self.parents()
        result: set[str] = set()
        stack, seen = [node], {node}
        while stack:
            current = stack.pop()
            above = up.get(current, [])
            if not above:
                result.add(current)
            for p in above:
                if p in memo:
                    result |= memo[p]
                elif p not in seen:
                    seen.add(p)
                    stack.append(p)
        memo[node] = frozenset(result)
        return memo[node]

    def roots_for_file(self, path: str) -> set[str]:
        out: set[str] = set()
        for node, _ in self.starts_for(path):
            out |= self.roots_of(node)
        return out

    def chains(self, start: str, limit: int = 500) -> list[list[str]]:
        """Ways up from `start` to a root, `start` first (at most `limit`)."""
        up = self.parents()
        out: list[list[str]] = []

        def walk(node: str, path: list[str]) -> None:
            if len(out) >= limit:
                return
            above = [p for p in up.get(node, []) if p not in path]
            if not above:
                out.append(path)
                return
            for p in above:
                walk(p, [*path, p])

        walk(start, [start])
        return out

    def which(self, path: str, limit: int = 500) -> list[dict[str, Any]]:
        """Ways from a file up to the roots, at most `limit` (roots_for_file has them all)."""
        rows: list[dict[str, Any]] = []
        for node, via in self.starts_for(path):
            for chain in self.chains(node, limit - len(rows)):
                rows.append({"via": via, "object": node, "chain": chain, "root": chain[-1]})
            if len(rows) >= limit:
                break
        return rows

    def stale_inputs(self, root: Path) -> list[str]:
        """Watched files changed, added back or gone since the graph was made."""
        changed = []
        for rel, mtime in self.inputs.items():
            path = Path(rel) if os.path.isabs(rel) else root / rel
            try:
                if path.stat().st_mtime_ns != mtime:
                    changed.append(rel)
            except OSError:
                changed.append(rel)
        return changed
