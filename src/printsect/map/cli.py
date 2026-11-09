"""`psect-map`: which module of a C project uses which, on its own.

    psect-map                  the whole picture, most used modules first
    psect-map focus MODULE     one module: who uses it, what it uses, its files
    psect-map who NAME         who uses a function, variable or file (or FILE:LINE)
    psect-map impact NAME      what a change to it can reach, level by level
    psect-map issues           cycles, tangled modules, links against the layers
    psect-map summary          what to read first
    psect-map save, diff       how the map has changed since a snapshot

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
from .analysis import _valid, difference, impact, issues, snapshot, summary
from .model import Map, build, classify

Modules = Callable[[Project], "dict[str, str] | None"]
Marks = Callable[[Project], dict[str, list[dict[str, Any]]]]
Builds = Callable[[Project, list[str]], "dict[str, Any] | None"]
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


def no_outputs(project: Project) -> dict[str, str] | None:
    return None


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
        groups: dict[str, str] | None = args.map_outputs(proj)
        if groups is None:
            raise PsectError("--by-out needs the build: psect deps, then psect map --by-out")
        return groups
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
    """The files a change starts from, and the one symbol if a symbol was named."""
    if _IDENTIFIER.fullmatch(name):
        found = code_map.symbol(name, 1)
        if found is None:
            raise PsectError(f"nothing here defines or uses {name!r}")
        files = [f for f, e in code_map.t.entries.items() for d in e.defs
                 if d[0] == name and not d[2]]
        if len(files) != 1:
            raise PsectError(f"{name!r} is {found['status']}: no one definition to start from")
        return files, name
    files = code_map.files_named(name)
    if len(files) != 1:
        raise PsectError(f"no one source file named {name!r}")
    return files, None


def _impact_text(data: dict[str, Any]) -> str:
    title = "rests on" if data["direction"] == "up" else "can reach"
    what = data["symbol"] or ", ".join(data["from"])
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
    build = None if args.no_build_info else args.map_builds(args.map_project, files)
    if build:
        data["build"] = build
    out(args, data, _impact_text(data))
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


def page_data(proj: Project) -> dict[str, Any]:
    """The map for the build's page: every file's module and who uses what. Paths are
    the same ones the build's graph uses, so the two join up."""
    from . import make_indexer

    index = make_indexer(proj.settings.get("map")).index(proj, lambda text: None)
    tables = build(index)
    code_map = Map(tables)
    return {"modules": dict(code_map.owner),
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


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def configure(parser: argparse.ArgumentParser, *, modules: Modules | None = None,
              marks: Marks | None = None, outputs: Modules | None = None,
              builds: Builds | None = None,
              rules: Callable[[Project], dict[str, Any]] | None = None) -> None:
    """Add the map options and actions to `parser` (which already has --json/--root).

    `modules(project)` gives {source file: module}, the files the build uses, or None;
    `marks(project)` gives {file: [{"n": 7, ...}]} to show where probes are.
    `outputs(project)` gives {source file: output} for --by-out;
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
    p.add_argument("name", help="a function or variable, or a source file")
    p.add_argument("--depth", type=int, default=0, metavar="N",
                   help="how many steps (default: all)")
    p.add_argument("--up", action="store_true",
                   help="what it rests on, rather than what it reaches")
    p.set_defaults(func=cmd_impact)
    p = sub.add_parser("issues", parents=[below],
                       help="cycles, tangled modules, links against layers")
    p.set_defaults(func=cmd_issues)
    p = sub.add_parser("summary", parents=[below], help="what to read first")
    p.add_argument("--near", metavar="NAME", help="also the way in to this name or file")
    p.set_defaults(func=cmd_summary)
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
