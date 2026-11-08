"""A single HTML page for browsing the dependency graph in a browser.

Everything is inside the file (no network, no scripts from elsewhere), so it opens
offline and can be handed to someone as it is. The data goes in as JSON inside a
<script type="application/json"> block and is only ever put on the page as text.
The page holds paths relative to the project, never the folder it sits in.
"""

from __future__ import annotations

import html
import json
from functools import cache
from importlib.resources import files
from typing import Any

from .graph import Graph


def _embed(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # Nothing in the data can close the script block or open a comment.
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


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
    return {"paths": paths, "objects": objects, "archives": archives, "outs": outs}


def render_html(
    graph: Graph,
    probes: dict[str, list[dict[str, Any]]],
    *,
    name: str,
    stale: list[str] | None = None,
) -> str:
    data = {
        "name": name,
        "generated": graph.generated,
        "command": graph.command,
        "probes": probes,
        "stale": stale or [],
        "headers_traced": graph.headers_traced,
        **_compact(graph),
    }
    return (
        _template().replace("__TITLE__", html.escape(f"{name} - build dependencies"))
        .replace("__DATA__", _embed(data))
    )


@cache
def _template() -> str:
    """viewer.html, shipped inside the package next to this module."""
    return files(__package__).joinpath("viewer.html").read_text(encoding="utf-8")
