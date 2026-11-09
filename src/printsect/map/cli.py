"""`psect-map`: which module of a C project uses which, on its own.

    psect-map                  the whole picture, most used modules first
    psect-map focus MODULE     one module: who uses it, what it uses, its files
    psect-map who NAME         who uses a function, variable or file (or FILE:LINE)
    psect-map impact NAME      what a change to it can reach, level by level
    psect-map path FROM TO     the way one name, file or module comes to use another
    psect-map unresolved       each name left off the map, and why
    psect-map issues           cycles, tangled modules, links against the layers
    psect-map summary          what to read first
    psect-map save, diff       how the map has changed since a snapshot
    psect-map html             a page to read the map in a browser

It reads the source and runs nothing, so it works before anything is set up. `psect map`
is the same tool inside printsect, which groups by the build's archives when the build
has been read, and marks where probes are. Another tool can do that too:
`configure(parser, modules=..., marks=...)`.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections.abc import Callable
from typing import Any

from .. import __version__
from ..core.cli import common, common_below, out, project, run
from ..core.config import Project
from ..core.errors import PsectError
from .analysis import _valid, difference, impact, issues, path, snapshot, summary, unresolved
from .model import Map, Tables, build, classify

Modules = Callable[[Project], "dict[str, str] | None"]
Marks = Callable[[Project], dict[str, list[dict[str, Any]]]]
Builds = Callable[[Project, list[str]], "dict[str, Any] | None"]
Outputs = Callable[[Project], "dict[str, list[str]] | None"]
MaskNames = Callable[[Project], list[str]]
DEFAULT_TOP = 30
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FILE_LINE = re.compile(r"(.+\.\w+):(\d+)")
_SNAPSHOT_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
_STATUS = {
    "one": "defined once",
    "same_name": "defined in several files; which one a file means cannot be told, "
                 "so these uses are not on the map",
    "static": "static, so only its own file can use it",
    "undefined": "no definition in the source read (it may be a macro, in a header or "
                 "in a library)",
}


def no_modules(project: Project) -> dict[str, str] | None:
    return None


def no_marks(project: Project) -> dict[str, list[dict[str, Any]]]:
    return {}


def no_outputs(project: Project) -> dict[str, list[str]] | None:
    return None


def no_names(project: Project) -> list[str]:
    return []


def _joined(owner: dict[str, list[str]]) -> dict[str, str]:
    """Each file's module: the outputs it ends up in make one name when there are several."""
    return {file: "+".join(modules) for file, modules in owner.items()}


def no_builds(project: Project, files: list[str]) -> dict[str, Any] | None:
    return None


def no_rules(project: Project) -> dict[str, Any]:
    return {}


def _list(text: str | None) -> list[str]:
    return [t.strip() for t in (text or "").split(",") if t.strip()]


def _grouping(args: argparse.Namespace, proj: Project) -> dict[str, str] | None:
    if args.no_build_info:
        return None
    if args.by_out:
        groups: dict[str, list[str]] | None = args.map_outputs(proj)
        if groups is None:
            raise PsectError("--by-out needs the build: psect deps, then psect map --by-out")
        return _joined(groups)
    if args.by_dir:
        return None
    grouped: dict[str, str] | None = args.map_modules(proj)
    return grouped


def _map(args: argparse.Namespace) -> Map:
    from . import make_indexer

    proj = project(args)
    settings = proj.settings.get("map") or {}
    indexer = make_indexer(settings)
    index = indexer.index(proj, lambda text: print(text, file=sys.stderr))
    args.map_project = proj
    args.map_indexer = indexer
    rules = args.map_rules(proj)
    # What the settings say comes before what a knowledge file says.
    classes = classify(index.files, {**(settings.get("classes") or {}),
                                     **{k: v for k, v in rules.get("classes", {}).items()
                                        if k not in (settings.get("classes") or {})}})
    args.map_layers = settings.get("layers") or rules.get("layers") or []
    args.map_classes = classes
    only, hide = set(_list(args.only)), set(_list(args.hide))
    for wanted in only | hide:
        if wanted not in set(classes.values()):
            raise PsectError(f"no file is of class {wanted!r}; these are: "
                             f"{', '.join(sorted(set(classes.values())))}")
    hidden = {f: c for f, c in classes.items() if (only and c not in only) or c in hide}
    modules = _grouping(args, proj)
    # An indexer of another kind may have no way to give lines; the answer then has none.
    reader = getattr(indexer, "lines_of", None)
    lines_in = (lambda file, name: reader(proj, file, name)) if reader else None
    return Map(build(index, modules, hidden), modules, args.map_marks(proj), lines_in)


def _more(section: dict[str, Any]) -> str:
    left = section["total"] - len(section["shown"])
    return f"  ... and {left} more (--top)" if left > 0 else ""


def _omitted(omitted: dict[str, int]) -> str:
    parts = [
        f"same name in several files {omitted['same_name_symbols']} symbol(s) "
        f"({omitted['same_name_refs']} use(s))" if omitted["same_name_symbols"] else "",
        f"static elsewhere {omitted['static_refs']}" if omitted["static_refs"] else "",
        f"named by a macro {omitted['macro_made_definitions']}"
        if omitted["macro_made_definitions"] else "",
        f"braces did not close {omitted['unbalanced_files']} file(s)"
        if omitted["unbalanced_files"] else "",
        f"declarations too long {omitted['too_long_declarations']}"
        if omitted["too_long_declarations"] else "",
        f"too large {omitted['too_large']}" if omitted["too_large"] else "",
        f"unreadable {omitted['unreadable']}" if omitted["unreadable"] else "",
        f"links not followed {omitted['links']}" if omitted["links"] else "",
        f"by map.exclude {omitted['excluded']} path(s)" if omitted["excluded"] else "",
        f"not in the build {omitted['not_in_build']} file(s)" if omitted["not_in_build"] else "",
    ]
    listed = "; ".join(p for p in parts if p)
    return f"left out: {listed}" if listed else "left out: nothing"


def _limits(data: dict[str, Any]) -> list[str]:
    """What the answer does not cover, and what it counts without being sure."""
    lines = [_omitted(data["omitted"])]
    if data.get("hidden"):
        taken = ", ".join(f"{k} {n}" for k, n in sorted(data["hidden"].items()))
        lines.append(f"filtered out, file(s) by class: {taken}")
    if data.get("confidence"):
        lines.append("uses by certainty: " + ", ".join(
            f"{k} {n}" for k, n in data["confidence"].items() if n))
    if data["inside_if"]:
        lines.append(f"counted though an #if may leave them out: {data['inside_if']} definition(s)")
    return lines


def _overview_text(data: dict[str, Any]) -> str:
    if not data["files"]:
        return "no C source files found (see map.extensions and map.exclude)"
    lines = [f"{data['files']} source file(s), {data['modules']['total']} module(s) "
             f"by {data['grouped_by']}, counts are (symbol, using file) pairs"]
    for m in data["modules"]["shown"]:
        probes = f"  probes {m['probes']}" if m["probes"] else ""
        lines.append(f"{m['name']}  {m['role']}  files {m['files']}  "
                     f"in {m['in']} ({m['used_by']})  out {m['out']} ({m['uses']}){probes}")
    if more := _more(data["modules"]):
        lines.append(more)
    lines.append("uses (user -> used):")
    lines += [f"  {e['from']} -> {e['to']}  {e['count']}" for e in data["edges"]["shown"]]
    if more := _more(data["edges"]):
        lines.append(more)
    lines += _cycles_text(data["cycles"])
    lines += _limits(data)
    return "\n".join(lines)


def _symbols(row: dict[str, Any]) -> str:
    names = [f"{s['name']} ({s['file']}:{s['line']})" for s in row["symbols"]]
    extra = f", +{row['more_symbols']}" if row["more_symbols"] else ""
    return ", ".join(names) + extra


def _cycles_text(cycles: dict[str, Any]) -> list[str]:
    if not cycles["total"]:
        return ["cycles: none"]
    lines = [f"cycles ({cycles['total']}), modules using each other, one way round each:"]
    for cycle in cycles["shown"]:
        modules = cycle["modules"]
        rest = modules["total"] - len(modules["shown"])
        names = ", ".join(modules["shown"]) + (f", +{rest}" if rest else "")
        lines.append(f"  {modules['total']} module(s): {names}")
        lines += [f"    {r['from']} -> {r['to']}  {r['count']}  {_symbols(r)}"
                  for r in cycle["round_trip"]]
    if more := _more(cycles):
        lines.append(more)
    return lines


def _focus_text(data: dict[str, Any]) -> str:
    lines = [f"{data['module']}  {data['files_in_module']} file(s), by {data['grouped_by']}"]
    for title, key in (("used from outside by", "from_outside"), ("uses outside", "to_outside")):
        lines.append(f"{title} ({data[key]['total']}):")
        lines += [f"  {r['module']}  {r['count']}  {_symbols(r)}" for r in data[key]["shown"]]
        if more := _more(data[key]):
            lines.append(more)
    lines.append(f"inside ({data['inside']['total']}):")
    lines += [f"  {r['from']} -> {r['to']}  {r['count']}  {_symbols(r)}"
              for r in data["inside"]["shown"]]
    if more := _more(data["inside"]):
        lines.append(more)
    lines.append("files:")
    for f in data["files"]["shown"]:
        entry = "  entry" if f["from_outside"] else ""
        probes = "  probes " + " ".join(f"#{n}" for n in f["probes"]) if f["probes"] else ""
        lines.append(f"  {f['file']}  {f['role']}{entry}  from outside {f['from_outside']}  "
                     f"to outside {f['to_outside']}{probes}")
    if more := _more(data["files"]):
        lines.append(more)
    lines += _limits(data)
    return "\n".join(lines)


def _used_at(row: dict[str, Any]) -> str:
    more = f", +{row['more_lines']}" if row["more_lines"] else ""
    at = f":{', '.join(str(n) for n in row['lines'])}{more}" if row["lines"] else ""
    return f"{row['file']}{at}"


def _who_text(data: dict[str, Any]) -> str:
    users = data["users"]
    if data["kind"] == "file":
        lines = [f"{data['name']}  in {data['module']}, defines {data['defines']}, "
                 f"used by {users['total']} file(s)"]
        lines += [f"  {r['file']}  {r['module']}  {r['count']}  {_symbols(r)}"
                  for r in users["shown"]]
    else:
        lines = [f"{data['name']}: {_STATUS[data['status']]} ({data['confidence']})"]
        if data.get("at"):
            lines[0] = f"{data['at']} is in {lines[0]}"
        if data["defined"]["total"]:
            lines.append("defined:")
            lines += [f"  {d['file']}:{d['line']}  {d['kind']}{'  static' if d['static'] else ''}"
                      for d in data["defined"]["shown"]]
            if more := _more(data["defined"]):
                lines.append(more)
        lines.append(f"used by ({users['total']}):")
        lines += [f"  {_used_at(r)}  {r['module']}{'  its own file' if r['own_file'] else ''}"
                  + (f"  {r['link']}" if r.get("link") else "")
                  for r in users["shown"]]
        if data["cannot_reach"]:
            lines.append(f"{data['cannot_reach']} other file(s) use this name, "
                         "and cannot reach a static")
    if more := _more(users):
        lines.append(more)
    if data["kind"] == "symbol":
        lines.append(f"not seen: {data['not_seen']}")
    return "\n".join(lines)


def _who(code_map: Map, name: str, top: int) -> dict[str, Any]:
    """A C name can hold no dot or slash and a source file's path does, so one look tells."""
    at_line = _FILE_LINE.fullmatch(name)
    if at_line:
        files = code_map.files_named(at_line.group(1))
        if len(files) != 1:
            raise PsectError(f"no one source file named {at_line.group(1)!r}")
        inside = code_map.at_line(files[0], int(at_line.group(2)))
        if inside is None:
            raise PsectError(f"nothing is defined at or above {name}")
        found = code_map.symbol(inside, top)
        assert found is not None
        found["at"] = f"{files[0]}:{at_line.group(2)}"
        return found
    if _IDENTIFIER.fullmatch(name):
        found = code_map.symbol(name, top)
        if found is None:
            near = difflib.get_close_matches(name, code_map.symbol_names(name[0]), n=3)
            hint = f"; did you mean {', '.join(near)}?" if near else ""
            raise PsectError(f"nothing here defines or uses {name!r}{hint}")
        return found
    files = code_map.files_named(name)
    if len(files) > 1:
        raise PsectError(f"{name!r} could be {', '.join(files[:5])}; give more of the path")
    if not files:
        near = difflib.get_close_matches(name, code_map.t.files, n=3, cutoff=0.5)
        hint = f"; did you mean {', '.join(near)}?" if near else ""
        raise PsectError(f"no source file named {name!r}{hint}")
    return code_map.file_users(files[0], top)


def cmd_who(args: argparse.Namespace) -> int:
    data = _who(_map(args), args.name, args.top)
    if args.links and data["kind"] == "symbol":
        root = args.map_project.root.as_posix()
        for row in data["users"]["shown"]:
            line = f":{row['lines'][0]}" if row["lines"] else ""
            row["link"] = f"vscode://file/{root}/{row['file']}{line}"
    out(args, data, _who_text(data))
    return 0


def _start(code_map: Map, name: str) -> tuple[list[str], str | None]:
    """The files a name stands for, and the one symbol if a symbol was named. A function
    or variable comes first, then a source file, then a module, archive or folder."""
    is_name = _IDENTIFIER.fullmatch(name)
    if is_name:
        found = code_map.symbol(name, 1)
        if found is not None:
            files = [f for f, e in code_map.t.entries.items() for d in e.defs
                     if d[0] == name and not d[2]]
            if len(files) != 1:
                raise PsectError(f"{name!r} is {found['status']}: no one definition to start from")
            return files, name
    else:
        files = code_map.files_named(name)
        if len(files) == 1:
            return files, None
    module = code_map.resolve(name)
    inside = [f for f in code_map.t.files if module and code_map._inside(module, f)]
    if inside:
        return inside, None
    raise PsectError(f"nothing here defines or uses {name!r}, and no module is named that"
                     if is_name else f"no one source file or module named {name!r}")


def _impact_text(data: dict[str, Any]) -> str:
    title = "rests on" if data["direction"] == "up" else "can reach"
    what = data["symbol"] or data.get("module") or ", ".join(data["from"])
    lines = [f"{what} {title} {data['files']['total']} file(s) in "
             f"{len(data['modules'])} module(s):"]
    lines += [f"  {r['depth']}  {r['file']}  {r['module']}  {', '.join(r['via'])}"
              for r in data["files"]["shown"]]
    if more := _more(data["files"]):
        lines.append(more)
    build = data.get("build")
    if build:
        for title, key in (("objects", "objects"), ("archives", "archives"), ("outputs", "outs"),
                           ("deployed", "deployed")):
            if build[key]:
                lines.append(f"the build makes again, {title}: {', '.join(build[key][:10])}")
    lines.append(f"not seen: {data['not_seen']}")
    return "\n".join(lines)


def cmd_impact(args: argparse.Namespace) -> int:
    code_map = _map(args)
    files, symbol = _start(code_map, args.name)
    data = impact(code_map, files, symbol, args.depth, args.up, args.top)
    if symbol is None and code_map.files_named(args.name) != files:
        data["module"] = args.name
    build = None if args.no_build_info else args.map_builds(args.map_project, files)
    if build:
        data["build"] = build
    out(args, data, _impact_text(data))
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    code_map = _map(args)
    data = path(code_map, _start(code_map, args.start)[0], _start(code_map, args.end)[0])
    data.update(start=args.start, end=args.end)
    if data["found"]:
        lines = [f"{args.start} comes to use {args.end} in {len(data['steps'])} step(s):"]
        lines += [f"  {s['from']} -> {s['to']}  {', '.join(s['via'])}"
                  + (f", +{s['more_via']}" if s["more_via"] else "") for s in data["steps"]]
    else:
        lines = [f"no way found from {args.start} to {args.end}"]
        if data["other_way_steps"] is not None:
            lines.append(f"the other way round there is one, in {data['other_way_steps']} step(s)")
    lines.append(f"not seen: {data['not_seen']}")
    out(args, data, "\n".join(lines))
    return 0


def cmd_unresolved(args: argparse.Namespace) -> int:
    data = unresolved(_map(args), args.top)

    def name(r: dict[str, Any]) -> str:
        where = ", ".join(f"{d['file']}:{d['line']}" for d in r["defined"])
        more = f", +{r['more_defined']}" if r["more_defined"] else ""
        return f"{r['name']}  used by {r['users']}  defined in {where}{more}"

    def file(r: dict[str, Any]) -> str:
        kinds = [f"{k} {r[k]}" for k in ("macro_made", "inside_if", "too_long") if r[k]]
        return "  ".join([r["file"], *kinds, *(["unbalanced"] if r["unbalanced"] else [])])

    lines = _pairs(f"same name ({data['same_name_why']})", data["same_name"], name)
    lines += _pairs(f"static only ({data['static_only_why']})", data["static_only"], name)
    lines += _pairs("files not read for certain", data["files"], file)
    lines.append(f"not seen: {data['not_seen']}")
    out(args, data, "\n".join(lines))
    return 0


def _pairs(title: str, section: dict[str, Any], row: Callable[[dict[str, Any]], str]) -> list[str]:
    if not section["total"]:
        return [f"{title}: none"]
    lines = [f"{title} ({section['total']}):"]
    lines += ["  " + row(r) for r in section["shown"]]
    if more := _more(section):
        lines.append(more)
    return lines


def cmd_issues(args: argparse.Namespace) -> int:
    code_map = _map(args)
    data = issues(code_map, [str(x) for x in args.map_layers], args.top)
    lines = _cycles_text(data["cycles"])
    lines += _pairs("tangled pairs (each uses the other)", data["mutual"],
                    lambda r: f"{r['a']} <-> {r['b']}  {r['a_uses_b']} / {r['b_uses_a']}")
    lines += _pairs(f"used by {data['many_means']} or more", data["shared"],
                    lambda r: f"{r['module']}  {r['used_by']}")
    lines += _pairs(f"uses {data['many_means']} or more", data["sprawling"],
                    lambda r: f"{r['module']}  {r['uses']}")
    if data["layers_set"]:
        lines += _pairs("against map.layers", data["against_layers"],
                        lambda r: f"{r['user']} -> {r['used']}  {', '.join(r['symbols'])}")
    else:
        lines.append("against map.layers: not checked (set map.layers, top layer first)")
    lines += _pairs("single-use links, perhaps not needed", data["single_use_links"],
                    lambda r: f"{r['from']} -> {r['to']}")
    lines += _limits(data)
    out(args, data, "\n".join(lines))
    return 0


def _snapshot_file(args: argparse.Namespace, proj: Project) -> Any:
    name = args.snapshot
    if not _SNAPSHOT_NAME.fullmatch(name):
        raise PsectError("a snapshot name is letters, digits, - and _")
    return proj.state_file(f"map.{name}.snapshot.json")


def cmd_save(args: argparse.Namespace) -> int:
    from ..core.fsutil import ensure_state_dir, write_atomic

    code_map = _map(args)
    proj = args.map_project
    path = _snapshot_file(args, proj)
    data = snapshot(code_map)
    ensure_state_dir(proj.state_dir)
    write_atomic(path, json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    out(args, {"file": path.name, "edges": len(data["edges"])},
        f"saved {path.name}: {len(data['files'])} file(s), {len(data['edges'])} link(s)")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    code_map = _map(args)
    path = _snapshot_file(args, args.map_project)
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PsectError(f"no snapshot to compare with: psect-map save first ({path.name})"
                         ) from exc
    if not _valid(old):
        raise PsectError(f"{path.name} is not a snapshot of this kind: psect-map save again")
    data = difference(old, snapshot(code_map), args.top)
    def link(r: dict[str, Any]) -> str:
        return f"{r['from']} -> {r['to']}  {r['count']}"

    lines = _pairs("new links", data["new_links"], link)
    lines += _pairs("gone links", data["gone_links"], link)
    lines += _pairs("grown modules", data["grown"],
                    lambda r: f"{r['module']}  {r['was']} -> {r['now']}")
    lines += _pairs("new cycles", data["new_cycles"], lambda r: ", ".join(r))
    was, now = data["unresolved"]["was"], data["unresolved"]["now"]
    lines.append(f"unresolved uses: {was} -> {now}")
    lines.append(f"files: +{data['files_added']['total']} -{data['files_removed']['total']}")
    out(args, data, "\n".join(lines))
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    code_map = _map(args)
    near = None
    if args.near:
        near, _symbol = _start(code_map, args.near)
    data = summary(code_map, args.map_classes, near, args.top)
    scope = data["scope"]
    lines = [f"{scope['files']} source file(s), {scope['modules']} module(s) by "
             f"{scope['grouped_by']}; {data['note']}"]

    def add(title: str, rows: list[dict[str, Any]], key: str) -> None:
        if rows:
            lines.append(f"{title}: " + ", ".join(f"{r['module']} ({r[key]})" for r in rows))

    add("central", data["central"], "in")
    add("entrances", data["entrances"], "out")
    add("depends on most", data["depends_most"], "uses")
    add("widest reach", data["widest_reach"], "reach")
    add("most unresolved", data["most_unresolved"], "uses")
    lines.append("files by class: " + ", ".join(f"{k} {n}" for k, n in data["classes"].items()))
    if data["product_uses_of_others"]:
        lines.append("product code uses: " + ", ".join(
            f"{k} {n}" for k, n in data["product_uses_of_others"].items()))
    lines.append(f"cycles: {data['cycles']}")
    if "way_in" in data:
        lines.append("way in: " + (" -> ".join(data["way_in"]) or "none found"))
    lines += _limits(data)
    out(args, data, "\n".join(lines))
    return 0


def page_data(proj: Project, by_archive: dict[str, str] | None = None,
              by_output: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """The map for the build's page: every file's module, by folder and by what the build
    groups it into when that is known, and who uses what. Paths are the same ones the
    build's graph uses, so the two join up."""
    from . import make_indexer

    index = make_indexer(proj.settings.get("map")).index(proj, lambda text: None)
    tables = build(index)
    code_map = Map(tables)
    grouped: dict[str, Any] = {k: v for k, v in (("by_archive", by_archive),
                                                 ("by_output", by_output)) if v}
    return {"modules": dict(code_map.owner), **grouped,
            "edges": [[a, b, sorted(names)[:5]] for (a, b), names in tables.edges.items()]}


def cmd_overview(args: argparse.Namespace) -> int:
    data = _map(args).overview(args.top)
    out(args, data, _overview_text(data))
    return 0


def cmd_focus(args: argparse.Namespace) -> int:
    code_map = _map(args)
    module = code_map.resolve(args.module)
    if module is None:
        near = difflib.get_close_matches(args.module, code_map.known_names(), n=3, cutoff=0.5)
        hint = f"; did you mean {', '.join(near)}?" if near else ""
        raise PsectError(f"no module or folder named {args.module!r}{hint}")
    data = code_map.focus(module, args.top)
    out(args, data, _focus_text(data))
    return 0


def _snapshots(proj: Project) -> dict[str, dict[str, Any]]:
    """The saved snapshots of the project, each as it is."""
    from ..core.fsutil import plain_or_absent

    found: dict[str, dict[str, Any]] = {}
    if not (plain_or_absent(proj.state_dir) and proj.state_dir.is_dir()):
        return found
    for file in sorted(proj.state_dir.glob("map.*.snapshot.json")):
        name = file.name[len("map."):-len(".snapshot.json")]
        saved: Any = None
        try:
            if plain_or_absent(file):
                saved = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if _SNAPSHOT_NAME.fullmatch(name) and _valid(saved):
            found[name] = saved
    return found


def cmd_html(args: argparse.Namespace) -> int:
    from ..core.cli import save_page
    from ..core.timeutil import now
    from .page import DIRECTORY, first_uses, page_data, render

    if args.public and args.links:
        raise PsectError("--links puts this folder's path on the page: leave it off a public copy")
    code_map = _map(args)
    proj, tables = args.map_project, code_map.t
    groupings: dict[str, dict[str, list[str]] | None] = {}
    if not args.by_dir:
        archives, outputs = args.map_modules(proj), args.map_outputs(proj)
        if archives:
            groupings["archive"] = {f: [m] for f, m in archives.items()}
        if outputs:
            groupings["output"] = outputs
    groupings[DIRECTORY] = None
    default = "output" if args.by_out and "output" in groupings else next(iter(groupings))
    parts: dict[str, Any] = {
        "layers": [str(x) for x in args.map_layers],
        "lines": first_uses(args.map_indexer, proj, tables),
        "marks": args.map_marks(proj), "classes": args.map_classes}
    if args.public:
        page, report = _public_page(args, proj, tables, groupings, default, parts)
    else:
        report = None
        now_snapshot = snapshot(code_map)
        changes = {name: difference(saved, now_snapshot, 1 << 30)
                   for name, saved in _snapshots(proj).items()}
        joined = {k: _joined(v) if v else None for k, v in groupings.items()}
        page = render(page_data(
            name=proj.root.name, generated=now(), tables=tables, groupings=joined,
            default=default, limits=_limits(code_map.overview(1)), changes=changes,
            root=proj.root.as_posix() if args.links else "", **parts), proj.root.name)
    path, opened = save_page(args, proj, "map.public.html" if args.public else "map.html", page)
    said = f"wrote {path}" + (" (opened in the browser)" if opened else "")
    if report:
        said += ("\nmasked: {names} name(s), {files_hidden} file name(s) hidden, "
                 "{symbols_hidden} symbol(s) hidden; snapshots left out").format(**report)
    out(args, {"file": str(path), "opened": opened, "masked": report}, said)
    return 0


def _public_page(args: argparse.Namespace, proj: Project, tables: Tables,
                 groupings: dict[str, dict[str, list[str]] | None], default: str,
                 parts: dict[str, Any]) -> tuple[str, dict[str, int]]:
    """The page with the names taken out, or an error if something would still go out."""
    from ..core.cli import script_json
    from ..core.masking import Masker, leaks
    from .page import masked, page_data, project_text, render

    settings = proj.settings.get("public") or {}
    masker = Masker([*(settings.get("mask") or []), *args.map_mask_names(proj), proj.root.name],
                    bool(args.hide_sources or settings.get("hide_sources")))
    # A folder name is worked out here: once the files are renamed there are no folders.
    real = {key: owner if owner else {f: [m] for f, m in Map(tables).owner.items()}
            for key, owner in groupings.items()}
    made = masked(masker, tables, real, parts["classes"], parts["layers"], parts["lines"],
                  parts["marks"])
    first = Map(made["tables"], made["groupings"][default])
    data = page_data(name="project", generated="", groupings=made["groupings"], default=default,
                     tables=made["tables"], classes=made["classes"], layers=parts["layers"],
                     limits=_limits(first.overview(1)), lines=made["lines"], marks=made["marks"],
                     changes={}, root="", public="placeholder", rank=made["rank"])
    report = {"names": 0, "files_hidden": 0, "symbols_hidden": 0, **masker.counts}
    data["public"] = (f"{report['names']} names masked, {report['files_hidden']} file names "
                      f"hidden, {report['symbols_hidden']} symbols hidden; snapshots left out.")
    found = leaks(script_json(data), masker.names, proj.root.as_posix(), own=project_text(data))
    if found:
        raise PsectError("not written: this would still go out: " + "; ".join(found)
                         + ". Add what is wrong to public.mask, or use --hide-sources")
    return render(data, "project"), report


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def configure(parser: argparse.ArgumentParser, *, modules: Modules | None = None,
              marks: Marks | None = None, outputs: Outputs | None = None,
              builds: Builds | None = None,
              rules: Callable[[Project], dict[str, Any]] | None = None,
              mask_names: MaskNames | None = None) -> None:
    """Add the map options and actions to `parser` (which already has --json/--root).

    `modules(project)` gives {source file: module}, the files the build uses, or None;
    `marks(project)` gives {file: [{"n": 7, ...}]} to show where probes are.
    `outputs(project)` gives {source file: [its outputs]} for --by-out;
    `mask_names(project)` gives names to take out of a public page;
    `builds(project, files)` gives what the build makes again from them;
    `rules(project)` gives {"classes": ..., "layers": ...} to use beside the settings.
    """
    from . import SECTIONS

    parser.add_argument("--top", type=_positive, default=DEFAULT_TOP, metavar="N",
                        help=f"rows per list (default {DEFAULT_TOP})")
    parser.add_argument("--by-dir", action="store_true",
                        help="group by folder even when the build's archives are known")
    parser.add_argument("--by-out", action="store_true",
                        help="group by the build's final outputs (under psect map)")
    parser.add_argument("--only", metavar="CLASSES", help="only these classes (a,b)")
    parser.add_argument("--hide", metavar="CLASSES", help="leave these classes out (a,b)")
    parser.set_defaults(func=cmd_overview, map_modules=modules or no_modules,
                        map_marks=marks or no_marks, map_outputs=outputs or no_outputs,
                        map_builds=builds or no_builds, map_rules=rules or no_rules,
                        map_mask_names=mask_names or no_names,
                        sections=SECTIONS)
    below = common_below()
    below.add_argument("--top", type=_positive, default=argparse.SUPPRESS, metavar="N",
                       help="rows per list")
    below.add_argument("--by-dir", action="store_true", default=argparse.SUPPRESS,
                       help="group by folder even when the build's archives are known")
    parser.add_argument("--no-build-info", action="store_true", help=argparse.SUPPRESS)  # agents
    below.add_argument("--by-out", action="store_true", default=argparse.SUPPRESS,
                       help="group by the build's final outputs")
    below.add_argument("--only", default=argparse.SUPPRESS, metavar="CLASSES",
                       help="only these classes (a,b)")
    below.add_argument("--hide", default=argparse.SUPPRESS, metavar="CLASSES",
                       help="leave these classes out (a,b)")
    sub = parser.add_subparsers(dest="action", title="actions")
    p = sub.add_parser("focus", parents=[below],
                       help="one module: who uses it, what it uses, its files")
    p.add_argument("module", help="a module or archive from the map, or any folder")
    p.set_defaults(func=cmd_focus)
    p = sub.add_parser("who", parents=[below], help="who uses a function, variable or file")
    p.add_argument("name", help="a function or variable, a source file, or FILE:LINE")
    p.add_argument("--links", action="store_true",
                   help="add a link that opens each use in VS Code")
    p.set_defaults(func=cmd_who)
    p = sub.add_parser("impact", parents=[below], help="what a change to a name or file can reach")
    p.add_argument("name", help="a function or variable, a source file, or a module")
    p.add_argument("--depth", type=int, default=0, metavar="N",
                   help="how many steps (default: all)")
    p.add_argument("--up", action="store_true",
                   help="what it rests on, rather than what it reaches")
    p.set_defaults(func=cmd_impact)
    p = sub.add_parser("path", parents=[below],
                       help="the way one name, file or module comes to use another")
    p.add_argument("start", help="a function or variable, a source file, or a module")
    p.add_argument("end", help="what it comes to use, named the same ways")
    p.set_defaults(func=cmd_path)
    p = sub.add_parser("unresolved", parents=[below], help="each name left off the map, and why")
    p.set_defaults(func=cmd_unresolved)
    p = sub.add_parser("issues", parents=[below],
                       help="cycles, tangled modules, links against layers")
    p.set_defaults(func=cmd_issues)
    p = sub.add_parser("summary", parents=[below], help="what to read first")
    p.add_argument("--near", metavar="NAME", help="also the way in to this name or file")
    p.set_defaults(func=cmd_summary)
    p = sub.add_parser("html", parents=[below], help="a page to read the map in a browser")
    p.add_argument("--out", metavar="FILE", help="where to write it")
    p.add_argument("--no-open", action="store_true", help="do not open it in the browser")
    p.add_argument("--links", action="store_true",
                   help="add links that open files in VS Code (puts this folder's path on it)")
    p.add_argument("--public", action="store_true",
                   help="a copy to hand on: names masked (public.mask), then checked")
    p.add_argument("--hide-sources", action="store_true",
                   help="with --public, also replace file and symbol names")
    p.set_defaults(func=cmd_html)
    p = sub.add_parser("save", parents=[below], help="keep the map as it is now, to compare later")
    p.add_argument("snapshot", nargs="?", default="last", help="a name for it (default: last)")
    p.set_defaults(func=cmd_save)
    p = sub.add_parser("diff", parents=[below], help="what changed since a saved snapshot")
    p.add_argument("snapshot", nargs="?", default="last", help="its name (default: last)")
    p.set_defaults(func=cmd_diff)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psect-map", parents=[common()],
        description="Which module of a C project uses which (reads the source, runs nothing).")
    parser.add_argument("--version", action="version", version=f"psect-map {__version__}")
    configure(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser(), argv)


if __name__ == "__main__":
    sys.exit(main())
