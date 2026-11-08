"""`psect-probe`: put print probes into C source, keep a list of them, take them out.

    psect-probe add FILE LINE [-p PURPOSE]   a probe below LINE, recorded in the ledger
    psect-probe list [--quickfix]            the probes in the source
    psect-probe verify                       check the ledger against the source
    psect-probe clear [GROUP]                take out the agent's probes
    psect-probe note N TEXT                  add to a probe's record

`psect probe` is the same tool inside printsect. Another tool can add its own
checks to `add` with `configure(sub, after_add=...)`: a function given the project
and the new probe that returns warnings to show.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import __version__
from ..core.cli import common, out, project, run
from ..core.config import Project
from . import SECTIONS

if TYPE_CHECKING:
    from .ops import Report

AfterAdd = Callable[[Project, Any], list[str]]


def cmd_add(args: argparse.Namespace) -> int:
    from .ops import add

    proj = project(args)
    text = args.text
    if args.text_file:
        text = Path(args.text_file).read_text(encoding="utf-8")
    result = add(proj, args.file, args.line, group=args.group, text=text,
                 purpose=args.purpose or "", expect=args.expect or "", by=args.by)
    p = result.probe
    for check in args.after_add:
        result.warnings.extend(check(proj, p))
    lines = [f"#{p.n} put in {p.file}:{p.line_hint}" + (f" ({p.function})" if p.function else "")]
    lines += ["  " + s for s in result.shown]
    lines += [f"warning: {w}" for w in result.warnings]
    data = {"probe": asdict(p), "shown": result.shown, "warnings": result.warnings}
    out(args, data, "\n".join(lines))
    return 0


def _rows(proj: Project, report: Report, group: str | None) -> list[dict[str, Any]]:
    from .ledger import Ledger, last_result

    ledger = Ledger(proj.state_dir)
    rows = []
    for f in report.found:
        if group is not None and f.mark.group != group:
            continue
        probe = ledger.probes.get(f.mark.n) if f.mark.n is not None else None
        mine = probe if probe is not None and probe.by == ("ai" if f.mark.ai else "user") else None
        rows.append({
            "n": f.mark.n,
            "file": f.file,
            "line": f.line,
            "group": f.mark.group,
            "by": "ai" if f.mark.ai else "user",
            "in_ledger": mine is not None,
            "function": mine.function if mine else None,
            "purpose": mine.purpose if mine else "",
            "last_result": last_result(mine) if mine else "",
            "text": f.text,
        })
    return rows


def _print_report(report: Report) -> None:
    for m in report.moved:
        print(f"note: #{m['n']} moved {m['from']} -> {m['to']}")
    for n in report.missing:
        print(f"note: #{n} is no longer in the source (marked missing)")
    for n in report.returned:
        print(f"note: #{n} is back in the source")
    for o in report.orphans:
        print(f"note: {o['file']}:{o['line']} has an agent mark not in the ledger (#{o['n']})")


def cmd_list(args: argparse.Namespace) -> int:
    from .ops import verify

    proj = project(args)
    report = verify(proj, everywhere=args.everywhere)
    rows = _rows(proj, report, args.group)
    if args.quickfix:
        for r in rows:
            who = "AI" if r["by"] == "ai" else "user"
            tag = f"[{who} #{r['n']}{' ' + r['group'] if r['group'] else ''}]"
            purpose = f" {r['purpose']}" if r["purpose"] else ""
            print(f"{proj.root / r['file']}:{r['line']}: {tag}{purpose}")
        return 1 if args.check and rows else 0
    if args.json:
        out(args, {"probes": rows, "verify": report.to_dict()}, "")
    else:
        if not rows:
            print("no probes")
        for r in rows:
            who = "AI" if r["by"] == "ai" else "--"
            fn = f" in {r['function']}()" if r["function"] else ""
            print(f"#{r['n'] or '-':<4} {who}  {r['file']}:{r['line']}{fn}  [{r['group'] or '-'}]")
            if r["purpose"]:
                print(f"       purpose: {r['purpose']}")
            if r["last_result"]:
                print(f"       last:    {r['last_result']}")
        _print_report(report)
    return 1 if args.check and rows else 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .ops import verify

    proj = project(args)
    report = verify(proj, everywhere=args.everywhere)
    if args.json:
        out(args, report.to_dict(), "")
    else:
        _print_report(report)
        ai = len([f for f in report.found if f.mark.ai])
        print(f"{ai} agent line(s), {len(report.found) - ai} hand-made line(s) in the source")
    return 0


def cmd_clear(args: argparse.Namespace) -> int:
    from .ops import clear

    proj = project(args)
    result = clear(proj, args.group, orphans=args.orphans)
    lines = [f"took out {', '.join('#' + str(n) for n in result.removed) or 'nothing'}"]
    for n in result.changed:
        lines.append(f"left #{n} in: its lines were edited by hand; take it out yourself")
    for f in result.skipped_files:
        lines.append(f"could not write {f}; its probes are still in")
    lines.append(f"{result.left} agent line(s) left in the source")
    out(args, asdict(result), "\n".join(lines))
    return 0 if not result.changed and not result.skipped_files else 1


def cmd_note(args: argparse.Namespace) -> int:
    from .ops import note

    proj = project(args)
    probe = note(proj, args.n, args.text, args.purpose, args.expect)
    out(args, asdict(probe), f"#{probe.n} updated")
    return 0


def hit_lines(hits: dict[int, Any], probes: dict[int, Any]) -> list[str]:
    """The hits as text, one block per probe in the list, hit or not, then strays."""
    lines = []
    for n in sorted(set(probes) | set(hits)):
        hit, probe = hits.get(n), probes.get(n)
        where = f"  {probe.file}:{probe.line_hint}" if probe else "  (not in the list)"
        purpose = f"  {probe.purpose}" if probe and probe.purpose else ""
        if not hit:
            lines.append(f"#{n:<4} not reached{where}{purpose}")
            continue
        lines.append(f"#{n:<4} {hit.hits} hit{'' if hit.hits == 1 else 's'} "
                     f"(lines {hit.first}-{hit.last}){where}{purpose}")
        for name, values in hit.values.items():
            more = " ..." if hit.more_values and len(values) >= 5 else ""
            lines.append(f"       {name} = {', '.join(values)}{more}")
    return lines


def cmd_hits(args: argparse.Namespace) -> int:
    from .hits import count, numbered
    from .ledger import Ledger
    from .ops import BANNER_GROUP

    proj = project(args)
    if args.log == "-":
        text = sys.stdin.read()
    else:
        text = Path(args.log).read_text(encoding="utf-8", errors="replace")
    hits = count(numbered(text), proj.prefix)
    probes = {p.n: p for p in Ledger(proj.state_dir).active() if p.group != BANNER_GROUP}
    data = {"hits": [h.to_dict() for h in hits.values()],
            "not_reached": sorted(set(probes) - set(hits))}
    out(args, data, "\n".join(hit_lines(hits, probes)) or "no probes and no hits")
    return 0


def marks(proj: Project) -> dict[str, list[dict[str, Any]]]:
    """The probes in the source, by file, for other tools to show (deps tree, pages)."""
    from .ledger import Ledger

    found: dict[str, list[dict[str, Any]]] = {}
    for p in Ledger(proj.state_dir).active():
        found.setdefault(p.file, []).append({"n": p.n, "group": p.group, "purpose": p.purpose})
    return found


def configure(
    sub: argparse._SubParsersAction[argparse.ArgumentParser], *,
    after_add: list[AfterAdd] | None = None,
) -> None:
    """Add the probe actions to `sub`."""
    p = sub.add_parser("add", parents=[common()], help="put a probe in below LINE")
    p.add_argument("file")
    p.add_argument("line", type=int, help="the probe goes below this line (0 = top)")
    p.add_argument("--group", "-g",
                   help="a name for related probes, to list or take out together (default: none)")
    p.add_argument("--text", "-t", help="the probe's code; {tag} {n} {where} are filled in")
    p.add_argument("--text-file", help="read the probe's code from a file")
    p.add_argument("--purpose", "-p", help="what this probe is to find out")
    p.add_argument("--expect", "-e", help="what a hit or a miss would mean")
    p.add_argument("--by", choices=["ai", "user"], default="ai")
    p.set_defaults(func=cmd_add, after_add=list(after_add or []), sections=SECTIONS)

    p = sub.add_parser("list", parents=[common()], help="the probes in the source")
    p.add_argument("group", nargs="?")
    p.add_argument("--quickfix", action="store_true", help="file:line: text, for editors")
    p.add_argument("--everywhere", action="store_true", help="search every source file")
    p.add_argument("--check", action="store_true", help="exit 1 if any probe is in")
    p.set_defaults(func=cmd_list, sections=SECTIONS)

    p = sub.add_parser("verify", parents=[common()], help="check the ledger against the source")
    p.add_argument("--everywhere", action="store_true")
    p.set_defaults(func=cmd_verify, sections=SECTIONS)

    p = sub.add_parser("clear", parents=[common()], help="take out the agent's probes")
    p.add_argument("group", nargs="?")
    p.add_argument("--orphans", action="store_true",
                   help="also take out agent marks the ledger does not know")
    p.set_defaults(func=cmd_clear, sections=SECTIONS)

    p = sub.add_parser("hits", parents=[common()],
                       help="count what each probe printed in a log (FILE, or - for stdin)")
    p.add_argument("log")
    p.set_defaults(func=cmd_hits, sections=SECTIONS)

    p = sub.add_parser("note", parents=[common()], help="add to a probe's record")
    p.add_argument("n", type=int)
    p.add_argument("text", nargs="?")
    p.add_argument("--purpose", "-p")
    p.add_argument("--expect", "-e")
    p.set_defaults(func=cmd_note, sections=SECTIONS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psect-probe",
        description="Put print probes into C source, keep a list of them, take them out.")
    parser.add_argument("--version", action="version", version=f"psect-probe {__version__}")
    configure(parser.add_subparsers(dest="action", required=True))
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser(), argv)


if __name__ == "__main__":
    sys.exit(main())
