"""A copy of the graph to hand on: names taken out, then checked before it is written.

Masking works on what it is told and what it can recognise, so it is followed by a check
of what is about to be written. A value the check finds stops the page; it is not trusted
to have been caught.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ..core.errors import PsectError
from .graph import Graph

_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/])")
_DRIVE_OR_HOME = re.compile(r"[A-Za-z]:[\\/][^\"\s,]*|(?:/home|/Users|/mnt|/opt|/srv)/[^\"\s,]+")
_SUFFIX = re.compile(r"\.[A-Za-z0-9+]{1,8}$")
_SECRET = re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key)\b\s*[=:]\s*[^\s\",]+")


class Masker:
    """Rewrites names the same way every time, and counts what it did."""

    def __init__(self, names: list[str], hide_sources: bool) -> None:
        for name in names:
            if not (isinstance(name, str) and name.strip()):
                raise PsectError(f"public.mask: expected non-empty text, got {name!r}")
        self.names = sorted(set(names), key=len, reverse=True)
        self.hide_sources = hide_sources
        self.counts: Counter[str] = Counter()
        self._labels = {n: f"name{i}" for i, n in enumerate(self.names, 1)}
        self._rule = (re.compile("|".join(re.escape(n) for n in self.names), re.I)
                      if self.names else None)
        self._by_lower = {n.lower(): self._labels[n] for n in self.names}
        self._files: dict[str, str] = {}
        self._modules: dict[str, str] = {}
        self._targets: dict[str, str] = {}

    def _one(self, match: re.Match[str]) -> str:
        self.counts["names"] += 1
        return self._by_lower[match.group().lower()]

    def text(self, value: str) -> str:
        return self._rule.sub(self._one, value) if self._rule else value

    def path(self, value: str) -> str:
        if _ABSOLUTE.match(value):
            self.counts["absolute_paths"] += 1
            value = "(outside)/" + re.split(r"[\\/]", value)[-1]
        return self.text(value)

    def file(self, value: str) -> str:
        """A source, header or object: its name too when source names are hidden."""
        if not self.hide_sources:
            return self.path(value)
        if value not in self._files:
            self.counts["files_hidden"] += 1
            # From the last part of the path only, and only if it looks like a suffix:
            # "acme.board/rx" must not give away "acme.board/rx".
            suffix = _SUFFIX.search(re.split(r"[\\/]", value)[-1])
            self._files[value] = f"file-{len(self._files) + 1}" + (suffix.group() if suffix else "")
        return self._files[value]

    def module(self, value: str) -> str:
        """A module of the map: folders and archives name the code as much as files do."""
        if not self.hide_sources:
            return self.path(value)
        return self._modules.setdefault(value, f"module-{len(self._modules) + 1}")

    def target(self, value: str) -> str:
        """Where something is deployed: hosts and paths on a target, never handed on."""
        self.counts["targets"] += value not in self._targets
        return self._targets.setdefault(value, f"target-{len(self._targets) + 1}")

    def symbols(self, names: list[str]) -> list[str]:
        return [] if self.hide_sources else [self.text(n) for n in names]

    def report(self) -> dict[str, int]:
        return {k: self.counts[k] for k in ("names", "absolute_paths", "files_hidden", "targets")}


def mask_graph(graph: Graph, masker: Masker) -> Graph:
    """The same shape with every name rewritten. Commands and reasons are free text a
    person wrote or a build printed, so they are left out rather than rewritten."""
    f, p = masker.file, masker.path

    def kept(info: dict[str, Any]) -> dict[str, Any]:
        return {"status": info["status"]} if info.get("status") else {}

    return Graph(
        objects={f(o): {"source": f(i["source"]) if i.get("source") else None,
                        "headers": [f(h) for h in i.get("headers", [])],
                        "include_dirs": ([] if masker.hide_sources
                                         else [p(d) for d in i.get("include_dirs", [])]),
                        **kept(i)}
                 for o, i in graph.objects.items()},
        archives={p(a): {"objects": [f(o) for o in i.get("objects", [])],
                         **({"prebuilt": True} if i.get("prebuilt") else {}), **kept(i)}
                  for a, i in graph.archives.items()},
        outs={p(o): {"objects": [p(x) if x in graph.outs else f(x) for x in i.get("objects", [])],
                     "archives": [p(x) for x in i.get("archives", [])],
                     "libs": [masker.text(x) for x in i.get("libs", [])], **kept(i)}
              for o, i in graph.outs.items()},
        generated="", command="(left out of a public copy)",
        headers_traced=graph.headers_traced, default_status=graph.default_status,
        made={f(g): {"from": [f(x) for x in i.get("from", [])],
                     **({"by": f(i["by"])} if i.get("by") else {})}
              for g, i in graph.made.items()},
        copies={masker.target(t): p(s) if (s in graph.outs or s in graph.archives) else f(s)
                for t, s in graph.copies.items()},
        excluded={f(x): "left out by a setting" for x in graph.excluded},
    )


def _outputs(value: str, masker: Masker) -> str:
    """A file that ends up in several outputs is grouped under all of them, joined by +.
    Each is its own path, and only the start of a path is looked at for being absolute."""
    return "+".join(masker.module(part) for part in value.split("+"))


def mask_map(data: dict[str, Any], masker: Masker) -> dict[str, Any]:
    grouped: dict[str, Any] = {
               key: {masker.file(k): masker.module(v) for k, v in data[key].items()}
               for key in ("modules", "by_archive") if key in data}
    if "by_output" in data:
        grouped["by_output"] = {masker.file(k): _outputs(v, masker)
                                for k, v in data["by_output"].items()}
    return {**grouped, "edges": [[masker.file(a), masker.file(b), masker.symbols(s)]
                      for a, b, s in data["edges"]]}


# The parts of a page that come from the project; the rest is the page's own vocabulary,
# where a short name to mask ("ui") would be found inside a word like "build".
_FROM_THE_PROJECT = ("name", "paths", "outs", "copies", "excluded", "map", "probes", "public")


def project_text(data: dict[str, Any]) -> str:
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, dict):
            for key, inner in value.items():
                if key not in ("modules", "by_archive", "by_output", "edges"):  # the map's own
                    found.append(str(key))
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)

    for key in _FROM_THE_PROJECT:
        walk(data.get(key))
    return "\n".join(found)


def leaks(text: str, names: list[str], root: str, own: str | None = None) -> list[str]:
    """What is still in `text` that must not go out: kinds and counts, never the values.
    Names to mask are looked for in `own` when given: the parts from the project."""
    found = []
    drives = len(_DRIVE_OR_HOME.findall(text))
    if drives:
        found.append(f"absolute paths: {drives}")
    secrets = len(_SECRET.findall(text))
    if secrets:
        found.append(f"something like a password or key: {secrets}")
    low = (text if own is None else own).lower()
    named = sum(low.count(n.lower()) for n in names)
    if named:
        found.append(f"names to mask: {named}")
    low = text.lower()
    if root and root.lower().replace("\\", "/") in low.replace("\\/", "/"):
        found.append("this project's folder")
    return found
