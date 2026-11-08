"""`psect-deps`: what each output of a C build is made from, on its own.

    psect-deps                      read the build (make -n) into .printsect/deps.json
    psect-deps tree [-f PATTERN]    outputs -> archives -> sources -> headers
    psect-deps which FILE           what a file is built into
    psect-deps find PATTERN         files whose path matches
    psect-deps html                 a page to browse and search it in a browser

`psect deps` is the same tool inside printsect, which also marks where probes are.
Another tool can do that too: `configure(parser, marks=...)` takes a function giving
{file: [{"n": 7, "group": "...", "purpose": "..."}]} for a project.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import __version__
from ..core.cli import common, common_below, out, project, run
from ..core.config import STATE_DIR, Project
from ..core.errors import PsectError
from .graph import Graph

Marks = Callable[[Project], dict[str, list[dict[str, Any]]]]


def no_marks(project: Project) -> dict[str, list[dict[str, Any]]]:
    return {}


def _load(args: argparse.Namespace, proj: Project) -> Graph:
    from . import DEPS_FILE, load, make_scanner, runs_unasked, save

    if not proj.state_file(DEPS_FILE).is_file():
        settings = proj.settings.get("deps")
        if not runs_unasked(settings):
            raise PsectError(
                f"no dependency data yet. This project sets its own deps.command "
                f"({(settings or {}).get('command')!r}), which is run only when asked: "
                f"run {args.deps_name} first")
        # First use: read the build now rather than send the person off to do it.
        print(f"note: no dependency data yet; reading the build ({args.deps_name}) ...",
              file=sys.stderr)
        graph = make_scanner(proj.settings.get("deps")).scan(proj)
        save(proj, graph)
        for w in graph.warnings:
            print(f"warning: {w}", file=sys.stderr)
        return graph
    graph = load(proj)
    stale = graph.stale_inputs(proj.root)
    if stale:
        shown = ", ".join(stale[:3]) + (" ..." if len(stale) > 3 else "")
        print(f"note: {shown} changed since the build was read; run {args.deps_name} again",
              file=sys.stderr)
    return graph


def _file_arg(proj: Project, name: str) -> str:
    """A path as typed (from the current folder) or as the graph keeps it."""
    path = Path(name)
    if path.exists():
        try:
            return proj.rel(path)
        except PsectError:
            return path.resolve().as_posix()
    return name.replace("\\", "/")


def cmd_scan(args: argparse.Namespace) -> int:
    from . import DEPS_FILE, DEPS_MD, make_scanner, save

    proj = project(args)
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
    page = render_html(graph, args.deps_marks(proj), name=proj.root.name,
                       stale=graph.stale_inputs(proj.root))
    if args.out:
        path = Path(args.out).resolve()
    else:
        ensure_state_dir(proj.state_dir)
        path = proj.state_dir / "deps.html"
    write_atomic(path, page)
    opened = False
    if not args.no_open and not args.json and sys.stdout.isatty():
        import webbrowser

        opened = webbrowser.open(path.as_uri())
    out(args, {"file": str(path), "opened": opened},
        f"wrote {path}" + (" (opened in the browser)" if opened else ""))
    return 0


def configure(parser: argparse.ArgumentParser, *, marks: Marks | None = None,
              name: str = "psect-deps") -> None:
    """Add the deps options and actions to `parser` (which already has --json/--root)."""
    parser.add_argument("--from-log", metavar="FILE",
                        help="read saved `make -n` output instead of running make (- = stdin)")
    parser.add_argument("--ascii", action="store_true", help=argparse.SUPPRESS)
    from . import SECTIONS

    parser.set_defaults(func=cmd_scan, deps_marks=marks or no_marks, deps_name=name,
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
