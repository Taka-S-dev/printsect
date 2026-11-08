"""`psect-deploy`: put a build on the target the way `deploy:` in the config says.

    psect-deploy [--run N]

Exit 0 when it is on, 1 when it failed, 3 when a person has to do something first
(the manual deployer). `psect deploy` is the same tool inside printsect.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Protocol

from .. import __version__
from ..core.cli import common, out, project, run
from ..core.config import Project


def cmd_deploy(args: argparse.Namespace) -> int:
    from dataclasses import asdict

    from . import OK, WAITING_USER, DeployContext, make_deployer

    proj = project(args)
    proj.require_device()
    settings = proj.settings.get("deploy") or {}
    artifact = (proj.settings.get("build") or {}).get("artifact")
    ask = None
    if sys.stdin.isatty() and not args.json:
        def ask(question: str) -> str | None:
            try:
                return input(question + " ")
            except EOFError:
                return None
    link = args.deploy_console(proj)
    try:
        ctx = DeployContext(
            root=proj.root,
            run=args.run,
            device=proj.device or "",
            artifact=proj.root / artifact if artifact else None,
            prefix=proj.prefix,
            ask=ask,
            wait_for=link.wait if link else None,  # psect links the console in
            send=link.send if link else None,
            log=(lambda s: print(s, file=sys.stderr)) if args.json else print,
        )
        result = make_deployer(settings).deploy(ctx)
    finally:
        if link:
            link.close()
    out(args, asdict(result), f"{result.status}: {result.message}")
    if result.status == OK:
        return 0
    return 3 if result.status == WAITING_USER else 1


class ConsoleLink(Protocol):
    """The target's console as a deploy uses it: watch for a line, send a command."""

    def wait(self, pattern: str, timeout: float) -> str | None: ...

    def send(self, text: str, timeout: float) -> str: ...

    def close(self) -> None: ...


def no_console(proj: Project) -> ConsoleLink | None:
    return None


def configure(parser: argparse.ArgumentParser, *,
              console: Callable[[Project], ConsoleLink | None] | None = None) -> None:
    """Add the deploy options to `parser`.

    `console(project)` gives the target's console, which psect passes when one is set
    up: a deploy watches it for the run banner and sends loading commands to it.
    Without it, the manual deployer asks a person and the ftp deployer hands its
    loading commands to one.
    """
    from . import SECTIONS

    parser.add_argument("--run", type=int, default=0, help="the run number the build carries")
    parser.set_defaults(func=cmd_deploy, sections=SECTIONS, deploy_console=console or no_console)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="psect-deploy", parents=[common()],
        description="Put the build on the target the way deploy: in printsect.yaml says.")
    parser.add_argument("--version", action="version", version=f"psect-deploy {__version__}")
    configure(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser(), argv)


if __name__ == "__main__":
    sys.exit(main())
