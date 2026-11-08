"""What every command line shares: common options, JSON answers, exit codes.

Exit codes: 0 ok, 1 failed, 2 configuration error, 3 waiting for a person.
Every command takes --json for a machine-readable answer and --root for the project.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any

from .config import CORE_SCHEMA, Project, Schema, check, load_project
from .errors import PsectError


def common() -> argparse.ArgumentParser:
    """--json and --root, for a command's own parser."""
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--json", action="store_true", help="answer in JSON")
    p.add_argument("--root", help="project root (default: found from the current folder)")
    p.add_argument("--device", help="the device to use (default: the one psect use chose)")
    return p


def common_below() -> argparse.ArgumentParser:
    """The same options for an action under a command that already has them.

    They have no defaults, so `cmd --json action` keeps the --json given before it:
    a default here would overwrite it.
    """
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="answer in JSON")
    p.add_argument("--root", default=argparse.SUPPRESS, help="project root")
    p.add_argument("--device", default=argparse.SUPPRESS, help="the device to use")
    return p


def out(args: argparse.Namespace, data: Any, text: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif text:
        print(text)


def _top_keys(value: Any, want: Any) -> Any:
    """A plugin section ({"type": str} only) is checked here for its type alone; the plugin
    checks the rest of its keys when it is made."""
    if isinstance(want, dict) and set(want) == {"type"} and isinstance(value, dict):
        return {k: v for k, v in value.items() if k == "type"}
    return value


def project(args: argparse.Namespace) -> Project:
    """The project, its settings checked: printsect's own and the running tool's.

    A tool names its sections with `parser.set_defaults(sections=...)`. Sections of
    other tools are left alone here, so a tool works in a project set up for all of them.
    """
    root = getattr(args, "root", None)
    proj = load_project(Path(root) if root else None, device=getattr(args, "device", None))
    schema: Schema = {**CORE_SCHEMA, **getattr(args, "sections", {})}
    known = {k: _top_keys(v, schema[k]) for k, v in proj.settings.items() if k in schema}
    check(known, schema)
    return proj


def run(parser: argparse.ArgumentParser, argv: list[str] | None = None) -> int:
    """Parse, run the chosen command, and turn printsect errors into exit codes."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):  # not when tests capture output
            stream.reconfigure(encoding="utf-8")
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except PsectError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
