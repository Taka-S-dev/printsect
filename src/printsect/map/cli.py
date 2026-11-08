"""`psect-map`: which module of a C project uses which, on its own.

    psect-map                  the whole picture, most used modules first
    psect-map focus MODULE     one module: who uses it, what it uses, its files

It reads the source and runs nothing, so it works before anything is set up. `psect map`
is the same tool inside printsect, which groups by the build's archives when the build
has been read, and marks where probes are. Another tool can do that too:
`configure(parser, modules=..., marks=...)`.
"""

from __future__ import annotations

import argparse
import difflib
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


def no_modules(project: Project) -> dict[str, str] | None:
    return None


def no_marks(project: Project) -> dict[str, list[dict[str, Any]]]:
    return {}


def _map(args: argparse.Namespace) -> Map:
    from . import make_indexer

    proj = project(args)
    index = make_indexer(proj.settings.get("map")).index(
        proj, lambda text: print(text, file=sys.stderr))
    modules = None if args.by_dir else args.map_modules(proj)
    return Map(build(index, modules), modules, args.map_marks(proj))


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
        f"not in the build {omitted['not_in_build']} file(s)" if omitted["not_in_build"] else "",
    ]
    listed = "; ".join(p for p in parts if p)
    return f"left out: {listed}" if listed else "left out: nothing"


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
    lines.append(_omitted(data["omitted"]))
    return "\n".join(lines)


def _symbols(row: dict[str, Any]) -> str:
    names = [f"{s['name']} ({s['file']}:{s['line']})" for s in row["symbols"]]
    extra = f", +{row['more_symbols']}" if row["more_symbols"] else ""
    return ", ".join(names) + extra


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
    lines.append(_omitted(data["omitted"]))
    return "\n".join(lines)


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
