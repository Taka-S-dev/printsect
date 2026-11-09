"""`psect-map`: which module of a C project uses which, on its own.

    psect-map                  the whole picture, most used modules first
    psect-map focus MODULE     one module: who uses it, what it uses, its files
    psect-map who NAME         who uses a function, variable or file

It reads the source and runs nothing, so it works before anything is set up. `psect map`
is the same tool inside printsect, which groups by the build's archives when the build
has been read, and marks where probes are. Another tool can do that too:
`configure(parser, modules=..., marks=...)`.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections.abc import Callable
from typing import Any

from .. import __version__
from ..core.cli import common, common_below, out, project, run
from ..core.config import Project
from ..core.errors import PsectError
from .model import Map, build

Modules = Callable[[Project], "dict[str, str] | None"]
Marks = Callable[[Project], dict[str, list[dict[str, Any]]]]
DEFAULT_TOP = 30
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
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


def _map(args: argparse.Namespace) -> Map:
    from . import make_indexer

    proj = project(args)
    indexer = make_indexer(proj.settings.get("map"))
    index = indexer.index(proj, lambda text: print(text, file=sys.stderr))
    modules = None if args.by_dir else args.map_modules(proj)
    # An indexer of another kind may have no way to give lines; the answer then has none.
    reader = getattr(indexer, "lines_of", None)
    lines_in = (lambda file, name: reader(proj, file, name)) if reader else None
    return Map(build(index, modules), modules, args.map_marks(proj), lines_in)


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
        lines = [f"{data['name']}: {_STATUS[data['status']]}"]
        if data["defined"]["total"]:
            lines.append("defined:")
            lines += [f"  {d['file']}:{d['line']}  {d['kind']}{'  static' if d['static'] else ''}"
                      for d in data["defined"]["shown"]]
            if more := _more(data["defined"]):
                lines.append(more)
        lines.append(f"used by ({users['total']}):")
        lines += [f"  {_used_at(r)}  {r['module']}{'  its own file' if r['own_file'] else ''}"
                  for r in users["shown"]]
        if data["cannot_reach"]:
            lines.append(f"{data['cannot_reach']} other file(s) use this name, "
                         "and cannot reach a static")
    if more := _more(users):
        lines.append(more)
    return "\n".join(lines)


def _who(code_map: Map, name: str, top: int) -> dict[str, Any]:
    """A C name can hold no dot or slash and a source file's path does, so one look tells."""
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
    out(args, data, _who_text(data))
    return 0


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
              marks: Marks | None = None) -> None:
    """Add the map options and actions to `parser` (which already has --json/--root).

    `modules(project)` gives {source file: module}, the files the build uses, or None;
    `marks(project)` gives {file: [{"n": 7, ...}]} to show where probes are.
    """
    from . import SECTIONS

    parser.add_argument("--top", type=_positive, default=DEFAULT_TOP, metavar="N",
                        help=f"rows per list (default {DEFAULT_TOP})")
    parser.add_argument("--by-dir", action="store_true",
                        help="group by folder even when the build's archives are known")
    parser.set_defaults(func=cmd_overview, map_modules=modules or no_modules,
                        map_marks=marks or no_marks, sections=SECTIONS)
    below = common_below()
    below.add_argument("--top", type=_positive, default=argparse.SUPPRESS, metavar="N",
                       help="rows per list")
    below.add_argument("--by-dir", action="store_true", default=argparse.SUPPRESS,
                       help="group by folder even when the build's archives are known")
    sub = parser.add_subparsers(dest="action", title="actions")
    p = sub.add_parser("focus", parents=[below],
                       help="one module: who uses it, what it uses, its files")
    p.add_argument("module", help="a module or archive from the map, or any folder")
    p.set_defaults(func=cmd_focus)
    p = sub.add_parser("who", parents=[below], help="who uses a function, variable or file")
    p.add_argument("name", help="a function or variable, or a source file (a.c, net/a.c)")
    p.set_defaults(func=cmd_who)


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
