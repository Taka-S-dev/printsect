"""`psect-deps`: what each output of a C build is made from, on its own.

    psect-deps                      read the build (make -n) into .printsect/deps.json
    psect-deps tree [-f PATTERN]    outputs -> archives -> sources -> headers
    psect-deps which FILE           what a file is built into
    psect-deps impact FILE          what a change to it makes again
    psect-deps find PATTERN         files whose path matches
    psect-deps html                 a page to browse and search it in a browser

`psect deps` is the same tool inside printsect, which also marks where probes are.
Another tool can do that too: `configure(parser, marks=...)` takes a function giving
{file: [{"n": 7, "group": "...", "purpose": "..."}]} for a project.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import Any

from .. import __version__
from ..core.cli import common, common_below, out, project, run
from ..core.config import STATE_DIR, Project
from ..core.errors import PsectError
from .graph import Graph

Marks = Callable[[Project], dict[str, list[dict[str, Any]]]]
MapData = Callable[[Project], "dict[str, Any] | None"]
MaskNames = Callable[[Project], list[str]]


def no_marks(project: Project) -> dict[str, list[dict[str, Any]]]:
    return {}


def no_map(project: Project) -> dict[str, Any] | None:
    return None


def no_names(project: Project) -> list[str]:
    return []


def _load(args: argparse.Namespace, proj: Project) -> Graph:
    from . import DEPS_FILE, load

    if not proj.state_file(DEPS_FILE).is_file():
        # Reading the build runs code even with make -n: $(shell ...) in a Makefile runs.
        # So a question never does it on the side; only the command that says so does.
        raise PsectError(f"no dependency data yet: run {args.deps_name} first to read the build")
    graph = load(proj)
    stale = graph.stale_inputs(proj.root)
    if stale:
        shown = ", ".join(stale[:3]) + (" ..." if len(stale) > 3 else "")
        print(f"note: {shown} changed since the build was read; run {args.deps_name} again",
              file=sys.stderr)
    return graph


def _file_arg(proj: Project, name: str) -> str:
    """A path as typed (from the current folder) or as the graph keeps it."""
    text = name.replace("\\", "/")
    if PureWindowsPath(name).anchor or text.startswith("/"):
        # Not a plain relative path, so it is only compared as text: asking whether a
        # network or device path exists would contact that server, which hands it this
        # PC's login, and an agent can pass this value.
        if not text.startswith(("//", "/?")):
            try:
                inside = os.path.relpath(os.path.normpath(name), proj.root)
                if not inside.startswith(".."):
                    return inside.replace("\\", "/")
            except ValueError:  # another drive
                pass
        return text
    path = Path(name)
    if path.exists():
        try:
            return proj.rel(path)
        except PsectError:
            return path.resolve().as_posix()
    return name.replace("\\", "/")


def _graph_text(name: str) -> str:
    try:
        data = sys.stdin.buffer.read() if name == "-" else Path(name).read_bytes()
        return data.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise PsectError(f"cannot read the graph file {name}: {exc}") from exc


def cmd_scan(args: argparse.Namespace) -> int:
    from . import DEPS_FILE, DEPS_MD, make_scanner, save
    from .imported import read_graph

    proj = project(args)
    if args.from_graph:
        graph = read_graph(_graph_text(args.from_graph), proj)
    else:
        log = None
        if args.from_log == "-":
            log = sys.stdin.read()
        elif args.from_log:
            log = Path(args.from_log).read_text(encoding="utf-8", errors="replace")
        graph = make_scanner(proj.settings.get("deps")).scan(proj, log=log)
    save(proj, graph)
    summary = {
        "outs": len(graph.final_outs()),
        "archives": len(graph.archives),
        "objects": len(graph.objects),
        "headers": len(graph.headers()),
        "headers_traced": graph.headers_traced,
        "warnings": graph.warnings,
        "file": f"{STATE_DIR}/{DEPS_FILE}",
    }
    text = (
        f"{summary['outs']} outputs, {summary['archives']} archives, {summary['objects']}"
        f" objects, {summary['headers']} headers -> {STATE_DIR}/{DEPS_FILE}, "
        f"{STATE_DIR}/{DEPS_MD}"
    )
    text += "".join(f"\nwarning: {w}" for w in graph.warnings)
    out(args, summary, text)
    return 0


def cmd_tree(args: argparse.Namespace) -> int:
    from ..core.term import use_color
    from .search import Matcher
    from .tree import TreeOptions, build_tree, render_tree

    proj = project(args)
    graph = _load(args, proj)
    find = Matcher(args.find, regex=args.regex) if args.find else None
    opts = TreeOptions(sources=args.sources, headers=args.headers, find=find,
                       probed=args.probed, only=args.name or [])
    marks = {f: [m["n"] for m in ms] for f, ms in args.deps_marks(proj).items()}
    nodes = build_tree(graph, marks, opts)
    if args.json:
        out(args, {"tree": [n.to_dict() for n in nodes]}, "")
    elif not nodes:
        print("nothing matches" if (find or args.probed or args.name) else "the graph is empty")
    else:
        print("\n".join(render_tree(nodes, ascii=args.ascii, color=use_color(), find=find)))
    return 0 if nodes else 1


def cmd_which(args: argparse.Namespace) -> int:
    from . import HEADERS_OFF_NOTE, is_header
    from .tree import render_which

    proj = project(args)
    graph = _load(args, proj)
    path = _file_arg(proj, args.file)
    rows = graph.which(path)
    roots = sorted(graph.roots_for_file(path))
    note = HEADERS_OFF_NOTE if is_header(path) and not graph.headers_traced else ""
    if args.json:
        out(args, {"file": path, "roots": roots, "chains": rows, "note": note}, "")
    elif not rows:
        print(f"{path} is not built into anything this build makes")
        if note:
            print(f"note: {note}")
    else:
        shown = rows if args.all else rows[:20]
        print("\n".join(render_which(graph, path, shown, ascii=args.ascii)))
        if len(shown) < len(rows):
            more = f"{len(rows) - len(shown)}{'+' if len(rows) >= 500 else ''}"
            print(f"... {more} more ways (--all lists up to 500)")
        print(f"built into: {', '.join(roots)}")
    return 0 if rows else 1


def cmd_impact(args: argparse.Namespace) -> int:
    proj = project(args)
    graph = _load(args, proj)
    path = _file_arg(proj, args.file)
    if not (graph.starts_for(path) or path in graph.made or any(
            path in [*i.get("from", []), i.get("by")] for i in graph.made.values())):
        raise PsectError(f"{path} is not part of this build")
    data = graph.impact(path, args.depth)
    data["status"] = graph.status_of(path)
    lines = [f"changing {path} ({data['status']}) makes again:"]
    for title, key in (("generated", "made"), ("objects", "objects"), ("archives", "archives"),
                       ("partial links", "parts"), ("outputs", "outs"), ("deployed", "deployed")):
        if data[key]:
            lines.append(f"{title} ({len(data[key])}):")
            lines += [f"  {p}" for p in data[key]]
    if len(lines) == 1:
        lines.append("  nothing this build makes")
    out(args, data, "\n".join(lines))
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    from .graph import HEADER, SOURCE
    from .search import Matcher
    from .tree import all_files

    proj = project(args)
    graph = _load(args, proj)
    find = Matcher(args.pattern, regex=args.regex)
    rows: list[dict[str, Any]] = []
    for f, kind in sorted(all_files(graph).items()):
        if find(f):
            rows.append({"file": f, "kind": kind, "roots": sorted(graph.roots_for_file(f))})
    if args.json:
        out(args, {"matches": rows}, "")
    elif args.quickfix:
        for r in rows:
            if r["kind"] not in (SOURCE, HEADER):
                continue
            chains = (" > ".join(Path(c).name for c in reversed(w["chain"]))
                      for w in graph.which(r["file"]))
            for chain in dict.fromkeys(chains):
                print(f"{proj.root / r['file']}:1: {chain}")
    else:
        for r in rows:
            into = ", ".join(r["roots"]) if r["roots"] else "-"
            print(f"{r['file']}  [{r['kind']}]  -> {into}")
    return 0 if rows else 1


def cmd_html(args: argparse.Namespace) -> int:
    from ..core.fsutil import ensure_state_dir, write_atomic
    from .html import render_html

    proj = project(args)
    graph = _load(args, proj)
    if args.public:
        page, report = _public_page(args, proj, graph)
    else:
        report = None
        page = render_html(graph, args.deps_marks(proj), name=proj.root.name,
                           stale=graph.stale_inputs(proj.root), map_data=args.deps_map(proj))
    if args.out:
        path = Path(args.out).resolve()
    else:
        ensure_state_dir(proj.state_dir)
        path = proj.state_dir / ("deps.public.html" if args.public else "deps.html")
    write_atomic(path, page)
    if path.is_relative_to(proj.root) and not path.is_relative_to(proj.state_dir):
        print(f"note: {path.name} is inside the project: keep it out of version control",
              file=sys.stderr)
    opened = False
    if not args.no_open and not args.json and sys.stdout.isatty():
        import webbrowser

        opened = webbrowser.open(path.as_uri())
    said = f"wrote {path}" + (" (opened in the browser)" if opened else "")
    if report:
        said += ("\nmasked: {names} name(s), {absolute_paths} absolute path(s), "
                 "{files_hidden} file name(s) hidden, {targets} deploy target(s) replaced; "
                 "commands, probe notes and reasons left out").format(**report)
    out(args, {"file": str(path), "opened": opened, "masked": report}, said)
    return 0


def _public_page(args: argparse.Namespace, proj: Project,
                 graph: Graph) -> tuple[str, dict[str, int]]:
    """The page with the names taken out, or an error if something would still go out."""
    from .html import embedded, page_data, render_data
    from .public import Masker, leaks, mask_graph, mask_map, project_text

    settings = proj.settings.get("public") or {}
    names = [*(settings.get("mask") or []), *args.deps_mask_names(proj), proj.root.name]
    masker = Masker(names, bool(args.hide_sources or settings.get("hide_sources")))
    masked = mask_graph(graph, masker)
    probes = {masker.file(f): [{"n": m.get("n")} for m in ms]
              for f, ms in args.deps_marks(proj).items()}
    map_data = args.deps_map(proj)
    report = {}
    data = page_data(masked, probes, name="project",
                     map_data=mask_map(map_data, masker) if map_data else None,
                     public="placeholder")
    report = masker.report()
    data["public"] = (f"{report['names']} names masked, {report['absolute_paths']} absolute "
                      f"paths replaced, {report['files_hidden']} file names hidden, "
                      f"{report['targets']} deploy targets replaced; commands and notes left out.")
    # The name of the project's folder masks as any other, but "project" is the page's own.
    found = leaks(embedded(data), masker.names, proj.root.as_posix(), own=project_text(data))
    if found:
        raise PsectError("not written: this would still go out: " + "; ".join(found)
                         + ". Add what is wrong to public.mask, or use --hide-sources")
    return render_data(data, "project"), report


def configure(parser: argparse.ArgumentParser, *, marks: Marks | None = None,
              name: str = "psect-deps", map_data: MapData | None = None,
              mask_names: MaskNames | None = None) -> None:
    """Add the deps options and actions to `parser` (which already has --json/--root)."""
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--from-log", metavar="FILE",
                        help="read saved `make -n` output instead of running make (- = stdin)")
    source.add_argument("--from-graph", metavar="FILE",
                        help="read a graph your own tool wrote, running nothing (- = stdin)")
    parser.add_argument("--ascii", action="store_true", help=argparse.SUPPRESS)
    from . import SECTIONS

    parser.set_defaults(func=cmd_scan, deps_marks=marks or no_marks, deps_name=name,
                        deps_map=map_data or no_map, deps_mask_names=mask_names or no_names,
                        sections=SECTIONS)
    below = common_below()
    below.add_argument("--ascii", action="store_true", default=argparse.SUPPRESS,
                       help="draw with plain characters")
    sub = parser.add_subparsers(dest="action", title="actions")

    p = sub.add_parser("tree", parents=[below], help="outputs -> archives -> sources")
    p.add_argument("name", nargs="*", help="only these outputs or archives")
    p.add_argument("--find", "-f", metavar="PATTERN",
                   help="keep only branches with a matching file (text, *.h, or --regex)")
    p.add_argument("--regex", action="store_true", help="PATTERN is a regular expression")
    p.add_argument("--sources", "-s", action="store_true", help="show .c files, not objects")
    p.add_argument("--headers", action="store_true", help="show every header too")
    p.add_argument("--probed", action="store_true", help="only branches holding a probe")
    p.set_defaults(func=cmd_tree)

    p = sub.add_parser("which", parents=[below], help="what a file is built into")
    p.add_argument("file")
    p.add_argument("--all", action="store_true", help="list every way, not the first 20")
    p.set_defaults(func=cmd_which)

    p = sub.add_parser("impact", parents=[below], help="what a change to a file makes again")
    p.add_argument("file")
    p.add_argument("--depth", type=int, default=0, metavar="N",
                   help="how many steps up to follow (default: all)")
    p.set_defaults(func=cmd_impact)

    p = sub.add_parser("find", parents=[below], help="files whose path matches")
    p.add_argument("pattern")
    p.add_argument("--regex", action="store_true", help="PATTERN is a regular expression")
    p.add_argument("--quickfix", action="store_true", help="file:1: chain, for editors")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("html", parents=[below],
                       help="a page to browse and search the tree in a browser")
    p.add_argument("--out", "-o", metavar="FILE",
                   help="where to write (default .printsect/deps.html)")
    p.add_argument("--no-open", action="store_true", help="write the page but do not open it")
    p.add_argument("--public", action="store_true",
                   help="a copy to hand on: names masked (public.mask), then checked")
    p.add_argument("--hide-sources", action="store_true",
                   help="with --public, file names go too, and symbols")
    p.set_defaults(func=cmd_html)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psect-deps", parents=[common()],
        description="What each output of a C build is made from (reads the Makefile).")
    parser.add_argument("--version", action="version", version=f"psect-deps {__version__}")
    configure(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser(), argv)


if __name__ == "__main__":
    sys.exit(main())
