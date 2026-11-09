"""A single HTML page for browsing the dependency graph in a browser.

Everything is inside the file (no network, no scripts from elsewhere), so it opens
offline and can be handed to someone as it is. The data goes in as JSON inside a
<script type="application/json"> block and is only ever put on the page as text.
The page holds paths relative to the project, never the folder it sits in.
"""

from __future__ import annotations

import html
from functools import cache
from importlib.resources import files
from typing import Any

from ..core.cli import script_json as _embed
from .graph import Graph


def _compact(graph: Graph) -> dict[str, Any]:
    """The graph with every path written once, in `paths`, and used by its number.

    A large project repeats the same header paths about a million times; numbers keep
    the page a few megabytes instead of tens.
    """
    paths: list[str] = []
    ids: dict[str, int] = {}

    def i(path: str) -> int:
        n = ids.get(path)
        if n is None:
            n = ids[path] = len(paths)
            paths.append(path)
        return n

    objects = [
        [i(o), i(info["source"]) if info.get("source") else -1,
         [i(d) for d in info.get("include_dirs", [])], [i(h) for h in info.get("headers", [])]]
        for o, info in graph.objects.items()
    ]
    archives = [
        [i(a), [i(o) for o in info.get("objects", [])]] for a, info in graph.archives.items()
    ]
    outs = [
        [i(o), [i(x) for x in info.get("objects", [])], [i(x) for x in info.get("archives", [])],
         list(info.get("libs", []))]
        for o, info in graph.outs.items()
    ]
    every = [*graph.objects, *graph.archives, *graph.outs]
    tables = {**graph.objects, **graph.archives, **graph.outs}
    labels = [[i(p), info.get("name", ""), info.get("origin", "")]
              for p, info in tables.items() if info.get("name") or info.get("origin")]
    status = [[i(p), s] for p in every if (s := graph.status_of(p)) != graph.default_status]
    made = [[i(g), [i(x) for x in info.get("from", [])], i(info["by"]) if info.get("by") else -1]
            for g, info in graph.made.items()]
    return {"paths": paths, "objects": objects, "archives": archives, "outs": outs,
            "default_status": graph.default_status, "status": status, "made": made,
            "labels": labels,
            "copies": [[target, i(source)] for target, source in graph.copies.items()],
            "excluded": [[i(p), why] for p, why in graph.excluded.items()]}


def render_html(
    graph: Graph,
    probes: dict[str, list[dict[str, Any]]],
    *,
    name: str,
    stale: list[str] | None = None,
    map_data: dict[str, Any] | None = None,
    public: str = "",
) -> str:
    return render_data(page_data(graph, probes, name=name, stale=stale, map_data=map_data,
                                 public=public), name)


def page_data(
    graph: Graph,
    probes: dict[str, list[dict[str, Any]]],
    *,
    name: str,
    stale: list[str] | None = None,
    map_data: dict[str, Any] | None = None,
    public: str = "",
) -> dict[str, Any]:
    """What goes into the page, kept apart so a public copy can be checked first."""
    return {
        "name": name,
        "generated": graph.generated,
        "command": graph.command,
        "probes": probes,
        "stale": stale or [],
        "headers_traced": graph.headers_traced,
        "map": map_data,
        "public": public,
        **_compact(graph),
    }


def embedded(data: dict[str, Any]) -> str:
    return _embed(data)


def render_data(data: dict[str, Any], name: str) -> str:
    return (
        _template().replace("__GRAPH_JS__", _script())
        .replace("__TITLE__", html.escape(f"{name} - build dependencies"))
        .replace("__DATA__", _embed(data))  # last: nothing in the data can act as a marker
    )


@cache
def _template() -> str:
    """viewer.html, shipped inside the package next to this module."""
    return files(__package__).joinpath("viewer.html").read_text(encoding="utf-8")


@cache
def _script() -> str:
    """The graph part of the page, kept apart because it is most of the code."""
    return files(__package__).joinpath("viewer_graph.js").read_text(encoding="utf-8")
