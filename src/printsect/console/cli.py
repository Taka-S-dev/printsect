"""`psect-console`: read what the target prints, send it commands, wait for a line.

    psect-console mark                     the number of the last line so far
    psect-console read [--since N]         the lines after N (--for SEC keeps reading)
    psect-console send TEXT                send a command, print the answer
    psect-console wait PATTERN [--since N] wait until a line matches (exit 1 on timeout)

Lines are numbered; keep the last number you read and pass it as --since next time,
so nothing is read twice. `psect console` is the same tool inside printsect.
"""

from __future__ import annotations

import argparse
import sys
import time

from .. import __version__
from ..core.cli import common, out, project, run
from .base import Console, Line


def _console(args: argparse.Namespace) -> Console:
    from . import make_console

    proj = project(args)
    proj.require_device()
    return make_console(proj.settings.get("console"))


def _missed_note(console: Console) -> str:
    n = console.missed
    return f"note: {n} line(s) went by before they could be read" if n else ""


def cmd_mark(args: argparse.Namespace) -> int:
    console = _console(args)
    try:
        n = console.mark()
    finally:
        console.close()
    out(args, {"last": n}, str(n))
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    console = _console(args)
    lines: list[Line] = []
    since = 0
    try:
        since = args.since if args.since is not None else 0
        deadline = time.monotonic() + (args.for_ or 0)
        while True:
            new = console.read(since)
            if new:
                since = new[-1].no
                lines += new
                if not args.json:
                    for ln in new:
                        print(f"{ln.no}: {ln.text}")
                    sys.stdout.flush()
            if time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        note = _missed_note(console)
    finally:
        console.close()
    if args.json:
        out(args, {"lines": [{"no": ln.no, "text": ln.text} for ln in lines], "last": since,
                   "missed": console.missed}, "")
    elif note:
        print(note, file=sys.stderr)
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    console = _console(args)
    try:
        answer = console.send(args.text, args.timeout)
    finally:
        console.close()
    out(args, {"text": args.text, "output": answer}, answer)
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    from . import wait_for

    console = _console(args)
    try:
        line = wait_for(console, args.pattern, args.timeout, since=args.since)
    finally:
        console.close()
    if line is None:
        out(args, {"found": False}, f"not seen within {args.timeout:g}s: {args.pattern}")
        return 1
    out(args, {"found": True, "no": line.no, "text": line.text}, f"{line.no}: {line.text}")
    return 0


def configure(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the console actions to `sub`."""
    from . import SECTIONS

    p = sub.add_parser("mark", parents=[common()], help="the number of the last line so far")
    p.set_defaults(func=cmd_mark, sections=SECTIONS)

    p = sub.add_parser("read", parents=[common()], help="the lines after --since")
    p.add_argument("--since", type=int, help="the last line number already read (default 0)")
    p.add_argument("--for", dest="for_", type=float, metavar="SEC",
                   help="keep reading new lines for this long")
    p.set_defaults(func=cmd_read, sections=SECTIONS)

    p = sub.add_parser("send", parents=[common()], help="send a command, print the answer")
    p.add_argument("text")
    p.add_argument("--timeout", type=float)
    p.set_defaults(func=cmd_send, sections=SECTIONS)

    p = sub.add_parser("wait", parents=[common()], help="wait until a line matches PATTERN")
    p.add_argument("pattern", help="a regular expression")
    p.add_argument("--timeout", type=float, default=60)
    p.add_argument("--since", type=int, help="look after this line (default: from now)")
    p.set_defaults(func=cmd_wait, sections=SECTIONS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psect-console",
        description="Read what the target prints, send it commands, wait for a line.")
    parser.add_argument("--version", action="version", version=f"psect-console {__version__}")
    configure(parser.add_subparsers(dest="action", required=True))
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser(), argv)


if __name__ == "__main__":
    sys.exit(main())
