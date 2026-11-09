"""A copy of the graph to hand on: names taken out, then checked before it is written.

Masking works on what it is told and what it can recognise, so it is followed by a check
of what is about to be written. A value the check finds stops the page; it is not trusted
to have been caught.
"""

from __future__ import annotations

from typing import Any

from ..core.masking import Masker, leaks
from .graph import Graph

__all__ = ["Masker", "leaks", "mask_graph", "mask_map", "project_text"]

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


def mask_map(data: dict[str, Any], masker: Masker) -> dict[str, Any]:
    grouped: dict[str, Any] = {
               key: {masker.file(k): masker.module(v) for k, v in data[key].items()}
               for key in ("modules", "by_archive") if key in data}
    if "by_output" in data:
        # A list, not a joined name: a path can hold any separator, and each must be masked.
        grouped["by_output"] = {masker.file(k): [masker.module(out) for out in v]
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
