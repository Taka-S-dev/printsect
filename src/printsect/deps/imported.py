"""Reading a build from a file the project's own tool wrote.

For a build make cannot describe (a list of targets, a script calling the compiler, a
build system of its own): a small converter, in any language, writes this file, and
nothing about that build has to live in printsect. Reading it runs nothing.
"""

from __future__ import annotations

import json
import posixpath
import re
from typing import Any

from ..core.config import Project
from ..core.errors import PsectError
from ..core.timeutil import now
from .graph import ARCHIVE, GRAPH_VERSION, IMPORTED, OUT, SCRIPT, STATUSES, Graph

_NOT_IN_A_PATH = re.compile(r"[\x00-\x1f\x7f-\x9f:]")  # terminals, and C: or a stream


def _path(value: Any, where: str) -> str:
    """A path inside the project, the way the graph keeps paths."""
    text = value.replace("\\", "/") if isinstance(value, str) else ""
    clean = posixpath.normpath(text) if text else ""
    if (clean in ("", ".") or _NOT_IN_A_PATH.search(text) or text.startswith("/")
            or ".." in clean.split("/")):
        raise PsectError(f"{where}: {value!r} is not a path inside the project, "
                         "relative to its root")
    return clean


def _mapping(item: Any, allowed: set[str], where: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise PsectError(f"{where}: expected an object with {', '.join(sorted(allowed))}")
    for key in item:
        if key not in allowed:
            raise PsectError(f"{where}: unknown field {key!r}; "
                             f"expected {', '.join(sorted(allowed))}")
    return item


def _paths(item: dict[str, Any], key: str, where: str) -> list[str]:
    values = item.get(key, [])
    if not isinstance(values, list):
        raise PsectError(f"{where}.{key}: expected a list")
    return [_path(v, f"{where}.{key}") for v in values]


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    section = data.get(key, {})
    if not isinstance(section, dict):
        raise PsectError(f"{key}: expected an object keyed by path")
    return section


_DESCRIBED = {"status", "name", "origin"}  # what any part of the build can say about itself


def _described(fields: dict[str, Any], info: dict[str, Any], where: str) -> dict[str, Any]:
    if "status" in fields:
        if fields["status"] not in STATUSES:
            raise PsectError(f"{where}.status: one of {', '.join(STATUSES)}")
        info["status"] = fields["status"]
    for key in ("name", "origin"):
        if key in fields:
            info[key] = _label(fields[key], f"{where}.{key}")
    return info


def _label(value: Any, where: str) -> str:
    """Free text that reaches terminals and pages: printable, one line."""
    if not (isinstance(value, str) and value and value.isprintable()):
        raise PsectError(f"{where}: expected plain text, got {value!r}")
    return value


def _object(item: Any, where: str) -> dict[str, Any]:
    fields = _mapping(item, {"source", "headers", *_DESCRIBED}, where)
    source = fields.get("source")
    info: dict[str, Any] = {"source": None if source is None else _path(source, f"{where}.source")}
    _described(fields, info, where)
    if "headers" in fields:
        info["headers"] = _paths(fields, "headers", where)
    return info


def _archive(item: Any, where: str) -> dict[str, Any]:
    fields = _mapping(item, {"objects", "prebuilt", "made_in", *_DESCRIBED}, where)
    info: dict[str, Any] = {"objects": _paths(fields, "objects", where)}
    _described(fields, info, where)
    if "prebuilt" in fields:
        if not isinstance(fields["prebuilt"], bool):
            raise PsectError(f"{where}.prebuilt: expected true or false")
        info["prebuilt"] = fields["prebuilt"]
    if "made_in" in fields:
        info["made_in"] = _path(fields["made_in"], f"{where}.made_in")
    return info


def _out(item: Any, where: str) -> dict[str, Any]:
    fields = _mapping(item, {"objects", "archives", "libs", "made_in", *_DESCRIBED}, where)
    info: dict[str, Any] = {"objects": _paths(fields, "objects", where),
                            "archives": _paths(fields, "archives", where),
                            "libs": _paths(fields, "libs", where)}
    _described(fields, info, where)
    if "made_in" in fields:
        info["made_in"] = _path(fields["made_in"], f"{where}.made_in")
    return info


def _made(item: Any, where: str) -> dict[str, Any]:
    fields = _mapping(item, {"from", "by"}, where)
    info: dict[str, Any] = {"from": _paths(fields, "from", where)}
    if "by" in fields:
        info["by"] = _path(fields["by"], f"{where}.by")
    return info


def _inputs(data: dict[str, Any], project: Project) -> dict[str, int]:
    """The files the converter read, so a change to them is noticed like a Makefile's."""
    found: dict[str, int] = {}
    for name in _paths(data, "inputs", "the graph file"):
        try:
            found[name] = (project.root / name).stat().st_mtime_ns
        except OSError as exc:
            raise PsectError(f"inputs: cannot read {name}: {exc.strerror or exc}") from exc
    return found


def _check_links(graph: Graph) -> None:
    """Every part named exists, and nothing is built from itself: the questions walk
    these links upward and downward, and a loop would never end."""
    containers = [*graph.outs, *graph.archives]
    for container in containers:
        for child in graph.children(container):
            if graph.kind(child) is None:
                raise PsectError(f"{container} is made from {child}, which is not listed; "
                                 "add it to objects, archives or outs")
    waiting = {c: len({x for x in graph.children(c) if graph.kind(x) in (OUT, ARCHIVE)})
               for c in containers}
    ready = [c for c, n in waiting.items() if not n]
    up = graph.parents()
    while ready:
        for parent in up.get(ready.pop(), []):
            waiting[parent] -= 1
            if not waiting[parent]:
                ready.append(parent)
    stuck = sorted(c for c, n in waiting.items() if n)
    if stuck:
        raise PsectError(f"{stuck[0]} is built from itself, directly or through others")
    for target, source in graph.copies.items():
        if graph.kind(source) is None and source not in graph.made:
            raise PsectError(f"{target} is a copy of {source}, which is not listed; "
                             "add it to objects, archives, outs or made")


def read_graph(text: str, project: Project) -> Graph:
    """The graph a converter wrote, checked; an error names the first thing wrong."""
    try:
        loaded = json.loads(text)
    except ValueError as exc:
        raise PsectError(f"the graph file is not JSON: {exc}") from exc
    data = _mapping(loaded, {"version", "objects", "archives", "outs", "inputs", "made",
                             "copies", "excluded"}, "the graph file")
    if data.get("version", GRAPH_VERSION) != GRAPH_VERSION:
        raise PsectError(f"the graph file is version {data['version']!r}; "
                         f"this printsect reads version {GRAPH_VERSION}")

    def read(key: str, one: Any) -> dict[str, dict[str, Any]]:
        return {_path(name, key): one(item, f"{key}[{name!r}]")
                for name, item in _section(data, key).items()}

    graph = Graph(objects=read("objects", _object), archives=read("archives", _archive),
                  outs=read("outs", _out), generated=now(), command=IMPORTED,
                  inputs=_inputs(data, project), default_status=SCRIPT,
                  made=read("made", _made),
                  # Where a file is deployed can be off the project: a label, not a path here.
                  copies={_label(k, "copies"): _path(v, f"copies[{k!r}]")
                          for k, v in _section(data, "copies").items()},
                  excluded={_path(k, "excluded"): _label(v, f"excluded[{k!r}]")
                            for k, v in _section(data, "excluded").items()})
    graph.headers_traced = any("headers" in info for info in graph.objects.values())
    if not graph.objects:
        graph.warnings.append("the graph file lists no objects")
    _check_links(graph)
    return graph
