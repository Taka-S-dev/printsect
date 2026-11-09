"""`psect`: the printsect tools in one command, joined up.

Each tool also runs on its own and knows nothing of the others:

    psect deps ...    = psect-deps ...     what each output is built from
    psect probe ...   = psect-probe ...    print probes in the source and their list
    psect deploy ...  = psect-deploy ...   put the build on the target

What only makes sense with several tools lives here and nowhere else: the deps tree
and page mark where probes are, and `probe add` warns about a probe in a header when
deps does not follow headers.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__
from .core.cli import common, out, run
from .core.config import Project, Schema
from .core.errors import PsectError

# -- links between tools -------------------------------------------------------------


def _probe_marks(proj: Project) -> dict[str, list[dict[str, Any]]]:
    from .probe.cli import marks

    return marks(proj)


def _archive_modules(proj: Project) -> dict[str, str] | None:
    """Each source file the build uses -> its archive, once the build has been read.

    Only the saved reading is looked at: the map runs nothing, so it never starts make.
    """
    from .core.errors import PsectError
    from .deps import DEPS_FILE, load

    if not proj.state_file(DEPS_FILE).is_file():
        return None
    owner: dict[str, str] = {}
    try:
        graph = load(proj)
        parents = graph.parents()
        for obj, info in sorted(graph.objects.items()):
            source = info.get("source")
            if source:
                held = sorted(parents.get(obj, []))
                archives = [p for p in held if p in graph.archives]
                owner.setdefault(source, (archives or held or ["(not in an archive)"])[0])
    except (PsectError, AttributeError, TypeError, KeyError):
        return None  # a saved reading that is not a graph: fall back to folders
    # The reading may have come with the project, and its names reach terminals and agents.
    if any(not (isinstance(n, str) and n.isprintable()) for pair in owner.items() for n in pair):
        return None
    return owner or None


def _output_modules(proj: Project) -> dict[str, str] | None:
    """Each source file -> the final outputs it ends up in, joined when more than one."""
    from .deps import DEPS_FILE, load

    if not proj.state_file(DEPS_FILE).is_file():
        return None
    try:
        graph = load(proj)
        owner = {s: "+".join(sorted(graph.roots_for_file(s))) or "(not in an output)"
                 for s in graph.sources()}
    except (PsectError, AttributeError, TypeError, KeyError):
        return None
    if any(not (isinstance(n, str) and n.isprintable()) for pair in owner.items() for n in pair):
        return None
    return owner or None


def _map_builds(proj: Project, files: list[str]) -> dict[str, Any] | None:
    """What the build makes again from these files; only the saved reading is looked at."""
    from .deps import DEPS_FILE, load

    if not proj.state_file(DEPS_FILE).is_file():
        return None
    try:
        graph = load(proj)
        found: dict[str, list[str]] = {"objects": [], "archives": [], "outs": [], "deployed": []}
        for file in files:
            hit = graph.impact(file)
            for key, names in found.items():
                names.extend(n for n in hit[key] if n not in names)
    except (PsectError, AttributeError, TypeError, KeyError):
        return None
    # The reading may have come with the project, and its names reach terminals and agents.
    if any(not (isinstance(n, str) and n.isprintable()) for ns in found.values() for n in ns):
        return None
    return found


def _page_map(proj: Project) -> dict[str, Any] | None:
    from .map.cli import page_data

    return page_data(proj, _archive_modules(proj), _output_modules(proj))


def _knowledge_names(proj: Project) -> list[str]:
    from .knowledge import load_knowledge

    found = load_knowledge(proj)
    if found.refused:  # it may be the file holding the names to mask
        listed = ", ".join(r["file"] for r in found.refused[:5])
        raise PsectError(f"not written: a knowledge file was refused ({listed}), and it may hold "
                         "names to mask: psect knowledge says why; fix or remove it")
    return found.mask


def _knowledge_rules(proj: Project) -> dict[str, Any]:
    from .knowledge import load_knowledge

    found = load_knowledge(proj)
    return {"classes": found.classes, "layers": found.layers}


def _warn_probe_in_untraced_header(proj: Project, probe: Any) -> list[str]:
    from .deps import HEADERS_OFF_NOTE, is_header

    deps = proj.settings.get("deps") or {}
    if is_header(probe.file) and deps.get("headers", True) is False:
        return [HEADERS_OFF_NOTE]
    return []


class _ConsoleLink:
    """The console tool, lent to a deploy: watch for the run banner, send loading
    commands. Where the console stands is noted on opening, before the deploy starts,
    so a banner printed while the deploy is still busy is not missed."""

    def __init__(self, settings: dict[str, Any]) -> None:
        from .console import make_console

        self.console = make_console(settings)
        self.since = self.console.mark()

    def wait(self, pattern: str, timeout: float) -> str | None:
        from .console import wait_for

        line = wait_for(self.console, pattern, timeout, since=self.since)
        return line.text if line else None

    def send(self, text: str, timeout: float) -> str:
        return self.console.send(text, timeout)

    def close(self) -> None:
        self.console.close()


def _console_link(proj: Project) -> _ConsoleLink | None:
    settings = proj.settings.get("console") or {}
    return _ConsoleLink(settings) if settings.get("type") else None


# -- commands of the suite itself -------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .core.config import CONFIG_NAME, LOCAL_CONFIG_NAME, STATE_DIR, init_template, language
    from .core.fsutil import ensure_state_dir

    root = Path(args.root or ".").resolve()
    root.mkdir(parents=True, exist_ok=True)
    made = []
    for name, template in ((CONFIG_NAME, "init_config.yaml"),
                           (LOCAL_CONFIG_NAME, "init_local.yaml")):
        if not (root / name).exists():  # never over a file someone already wrote
            text = init_template(template, language(args.lang))
            (root / name).write_text(text, encoding="utf-8", newline="\n")
            made.append(name)
    if not os.path.lexists(root / STATE_DIR):
        ensure_state_dir(root / STATE_DIR)
        made.append(STATE_DIR + "/")
    text = "made " + ", ".join(made) if made else "already set up"
    text += (
        f"\nkeep {LOCAL_CONFIG_NAME} out of version control (git: add it to .gitignore; svn:"
        f" svn propset svn:ignore); {STATE_DIR}/ ignores itself for git"
        f"\nnext: fill in {CONFIG_NAME} from the top and run psect doctor as you go"
    )
    out(args, {"root": str(root), "made": made}, text)
    return 0


TOOLS = {
    "console": "psect-console",
    "deps": "psect-deps",
    "probe": "psect-probe",
    "deploy": "psect-deploy",
    "map": "psect-map",
    "knowledge": "psect-knowledge",
}


def _run_sections() -> Schema:
    from . import console, deploy, deps, knowledge, map, probe
    from .suite.run import SECTIONS

    return {**console.SECTIONS, **deps.SECTIONS, **probe.SECTIONS, **deploy.SECTIONS,
            **map.SECTIONS, **knowledge.SECTIONS, **SECTIONS}


def _ask_here(args: argparse.Namespace) -> Callable[[str], str | None] | None:
    """A person at this terminal, unless answers go to a program (--json, no terminal)."""
    if args.json or not sys.stdin.isatty():
        return None

    def ask(question: str) -> str | None:
        try:
            return input(question + " ")
        except EOFError:
            return None

    return ask


def _print_run(result: Any, prefix: str) -> None:
    from .probe.cli import hit_lines
    from .probe.ledger import Ledger
    from .suite.run import OK

    print(f"run {result.run}: {result.status} at {result.stage}"
          + (f" ({result.stopped_by})" if result.stopped_by else ""))
    if result.message:
        print(result.message)
    if result.status == OK and result.stage == "done":
        probes = {p.n: p for p in Ledger(Path(result.folder).parents[1]).active()
                  if p.group != "run"}
        print(f"{result.lines} line(s) read; outputs: {', '.join(result.artifacts) or '-'}")
        print("\n".join(hit_lines(result.hits, probes)) or "no probes")
    for w in result.warnings:
        print(f"warning: {w}")
    print(f"saved in {result.folder}")


def cmd_run(args: argparse.Namespace) -> int:
    from .core.cli import project
    from .deploy import WAITING_USER
    from .suite.run import OK, Hooks, run

    proj = project(args)
    log = (lambda s: print(s, file=sys.stderr)) if args.json else print
    result = run(proj, Hooks(ask=_ask_here(args), log=log), build=not args.no_build,
                 deploy=not args.no_deploy, resume=args.resume)
    if args.json:
        out(args, result.to_dict(), "")
    else:
        _print_run(result, proj.prefix)
    if result.status == OK:
        return 0
    return 3 if result.status == WAITING_USER else 1


def cmd_build(args: argparse.Namespace) -> int:
    """The build for the probes in now: shown with --plan, else run (as psect run does)."""
    import time

    from .core.cli import project
    from .suite.build import execute, outdated, plan

    proj = project(args)
    proj.require_device()
    log = (lambda s: print(s, file=sys.stderr)) if args.json else print
    if (args.all or args.file) and not args.plan:
        from .core.errors import ConfigError

        raise ConfigError("--all and --file only make a plan to look at: add --plan")
    planned = plan(proj, 0, log, files=args.file or None, everything=args.all)
    lines = [f"device:   {proj.device or '-'}",
             f"for:      {', '.join(planned.files) or '(no probes in)'}",
             f"archives: {', '.join(planned.archives) or '-'}",
             f"outs:     {', '.join(planned.outs) or '-'}",
             f"deploy:   {', '.join(planned.artifacts) or '-'}"]
    lines += [f"{i:>3}. {s.text()}    # {s.label}" for i, s in enumerate(planned.steps, 1)]
    if args.plan:
        out(args, planned.to_dict(), "\n".join(lines))
        return 0
    if not args.json:
        print("\n".join(lines[:5]))
    started = time.time()
    failed = execute(proj, planned.steps, proj.state_file("build.log"), log)
    if not failed and planned.steps:
        old = outdated(proj, planned.artifacts, started)
        if old:
            failed = "the build passed, but these were not made anew: " + ", ".join(old)
    out(args, {**planned.to_dict(), "ok": failed is None, "message": failed or ""},
        failed or "build ok")
    return 1 if failed else 0


def cmd_hits(args: argparse.Namespace) -> int:
    from .core.cli import project
    from .core.fsutil import require_plain
    from .probe.cli import hit_lines
    from .probe.hits import count
    from .probe.ledger import Ledger
    from .suite.run import last_run, run_folder

    proj = project(args)
    n = args.run or last_run(proj)
    log = require_plain(run_folder(proj, n) / "console.log") if n else None
    if log is None or not log.is_file():
        raise PsectError("no run has read the console yet: psect run")
    lines = []
    for raw in log.read_text(encoding="utf-8").splitlines():
        no, _, text = raw.partition("\t")
        lines.append((int(no), text))
    hits = count(lines, proj.prefix)
    probes = {p.n: p for p in Ledger(proj.state_dir).active() if p.group != "run"}
    out(args, {"run": n, "hits": [h.to_dict() for h in hits.values()],
               "not_reached": sorted(set(probes) - set(hits))},
        f"run {n}\n" + ("\n".join(hit_lines(hits, probes)) or "no probes and no hits"))
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    import io

    from .suite.mcp import serve

    # The protocol is UTF-8 with LF line ends on every system (not the Windows code page).
    if isinstance(sys.stdin, io.TextIOWrapper):
        sys.stdin.reconfigure(encoding="utf-8")
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    from .core.config import find_root

    # Fixed once here: every tool call runs in this project and no other.
    root = Path(args.root).resolve() if args.root else find_root(Path.cwd())
    return serve(str(root))


def cmd_guide(args: argparse.Namespace) -> int:
    from .core.config import language, load_project
    from .suite.mcp import guide

    try:
        settings = load_project(Path(args.root) if args.root else None).settings
    except PsectError:
        settings = {}  # the guide is read to fix the settings: never let them stop it
    text = guide(args.topic, language(args.lang, settings))
    out(args, {"topic": args.topic, "text": text}, text)
    return 0


def cmd_use(args: argparse.Namespace) -> int:
    """Show the devices set up and the one in use, or choose one for this project."""
    from .core.config import DEVICE_FILE, load_project
    from .core.fsutil import ensure_state_dir, write_atomic

    proj = load_project(Path(args.root) if args.root else None)
    if args.clear:
        (proj.state_dir / DEVICE_FILE).unlink(missing_ok=True)
        proj = load_project(proj.root)
    elif args.name:
        if args.name not in proj.devices:
            known = ", ".join(proj.devices) or "none (add devices: to printsect.yaml)"
            raise PsectError(f"no device {args.name!r}; set up: {known}")
        ensure_state_dir(proj.state_dir)
        write_atomic(proj.state_dir / DEVICE_FILE, args.name + "\n")
        proj = load_project(proj.root)
    lines = [f"{'*' if d == proj.device else ' '} {d}" for d in proj.devices]
    if not proj.devices:
        lines = ["no devices set up (devices: in printsect.yaml)"]
    elif proj.device is None:
        lines.append("none chosen: psect use NAME")
    out(args, {"device": proj.device, "devices": list(proj.devices)}, "\n".join(lines))
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    """Show the commands in the settings and record that a person approved them."""
    from .suite.approve import approve, commands, digest, unapproved, visible

    proj = load_project_for(args)
    shown = digest(proj)  # what the person sees is what gets signed, or nothing
    listed = commands(proj)  # control characters already made visible
    waiting = unapproved(proj)
    text = "\n".join(f"  {c}" for c in listed)
    if args.show or not waiting:
        state = "approved" if not waiting else "NOT approved"
        out(args, {"commands": listed, "approved": not waiting}, f"{state}:\n{text}")
        return 0
    # A person at a terminal, and only that: no flag, no pipe, so no program can approve.
    if args.json or not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise PsectError("approving needs a person at a terminal: run psect approve there")
    print(f"for an agent, printsect will run these when it reads the build, builds, "
          f"deploys, reads the console or reproduces"
          f"{' (device ' + visible(proj.device) + ')' if proj.device else ''}:\n{text}")
    if input("approve them? [y/N] ").strip().lower() not in ("y", "yes"):
        print("not approved")
        return 1
    approve(proj, expected=shown)
    out(args, {"commands": listed, "approved": True}, "approved")
    return 0


def load_project_for(args: argparse.Namespace) -> Any:
    from .core.config import load_project

    return load_project(Path(args.root) if args.root else None,
                        device=getattr(args, "device", None))


def cmd_check(args: argparse.Namespace) -> int:
    """Every section of the settings, for every tool, and the plugins they pick."""
    from .core.config import load_project
    from .suite.doctor import settings_problems

    proj = load_project(Path(args.root) if args.root else None, device=args.device)
    found = settings_problems(proj)
    if found:
        out(args, {"ok": False, "problems": found},
            "problems in the settings:\n" + "\n".join(f"  {p}" for p in found))
        return 2
    out(args, {"ok": True, "problems": []}, f"settings ok ({proj.root})")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Each part of `psect run` tried for real, without building or touching the target."""
    from .core.config import load_project
    from .suite.doctor import NG, diagnose

    proj = load_project(Path(args.root) if args.root else None, device=args.device)
    found = diagnose(proj, run_commands=not args.no_commands)
    if args.json:
        out(args, {"root": str(proj.root), "findings": [f.to_dict() for f in found]}, "")
    else:
        for f in found:
            print(f"{f.status:<4} {f.name:<10} {f.message}")
            if f.hint:
                print(f"{'':16}-> {f.hint}")
    return 1 if any(f.status == NG for f in found) else 0


def cmd_plugins(args: argparse.Namespace) -> int:
    from . import console, deploy, deps, map  # noqa: F401  (registers builtins)
    from .core.plugins import available

    roles = {role: available(role) for role in ("console", "deployer", "deps", "indexer")}
    lines = ["tools (each also runs on its own):"]
    lines += [f"  psect {name:<8} = {command}" for name, command in TOOLS.items()]
    for role, names in roles.items():
        lines.append(f"{role}:")
        lines += [f"  {name:<12} {where}" for name, where in sorted(names.items())]
    out(args, {"tools": TOOLS, "roles": roles}, "\n".join(lines))
    return 0


def build_parser() -> argparse.ArgumentParser:
    from .console import cli as console_cli
    from .deploy import cli as deploy_cli
    from .deps import cli as deps_cli
    from .knowledge import cli as knowledge_cli
    from .map import cli as map_cli
    from .probe import cli as probe_cli

    parser = argparse.ArgumentParser(
        prog="psect", description="Narrow down a C bug with print probes: the printsect tools.")
    parser.add_argument("--version", action="version", version=f"psect {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", parents=[common()], help="set up printsect.yaml and .printsect/")
    p.add_argument("--lang", help="the language of the comments: en or ja (default: PSECT_LANG)")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("run", parents=[common()],
                       help="one turn: build, deploy, reproduce, read the console, count hits")
    p.add_argument("--resume", action="store_true",
                   help="carry on a run that stopped for a person (exit 3)")
    p.add_argument("--no-build", action="store_true", help="use the outputs as they are")
    p.add_argument("--no-deploy", action="store_true", help="the target already runs them")
    p.set_defaults(func=cmd_run, sections=_run_sections())
    p = sub.add_parser("build", parents=[common()],
                       help="build what the probes in need, by build.steps (--plan: show only)")
    p.add_argument("--plan", action="store_true", help="show the commands, do not run them")
    p.add_argument("--all", action="store_true",
                   help="plan as if every source held a probe (to agree the commands first)")
    p.add_argument("--file", action="append", metavar="FILE",
                   help="plan as if FILE held a probe (repeatable)")
    p.set_defaults(func=cmd_build, sections=_run_sections())
    p = sub.add_parser("hits", parents=[common()], help="what each probe printed in a run")
    p.add_argument("run", type=int, nargs="?", help="the run number (default: the last)")
    p.set_defaults(func=cmd_hits, sections=_run_sections())
    p = sub.add_parser("mcp", help="serve printsect to an AI agent over MCP (stdio)")
    p.add_argument("--root", help="the project (default: found from the current folder)")
    p.set_defaults(func=cmd_mcp)
    p = sub.add_parser("guide", help="guides: loop (finding a bug), setup, settings")
    p.add_argument("topic", nargs="?", default="loop", choices=["loop", "setup", "settings"])
    p.add_argument("--lang", help="en or ja (default: PSECT_LANG, else lang: in the settings)")
    p.add_argument("--json", action="store_true", help="answer in JSON")
    p.add_argument("--root", help=argparse.SUPPRESS)  # every tool call passes one
    p.add_argument("--device", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_guide)
    p = sub.add_parser("doctor", parents=[common()],
                       help="try each part psect run needs (puts nothing on the target)")
    p.add_argument("--no-commands", action="store_true", help=argparse.SUPPRESS)  # agents
    p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("approve", parents=[common()],
                       help="approve the commands in the settings, so an agent may run them")
    p.add_argument("--show", action="store_true", help="list them and whether they are approved")
    p.set_defaults(func=cmd_approve)
    p = sub.add_parser("use", help="the devices set up, and choose the one to use")
    p.add_argument("name", nargs="?", help="the device to use from now on")
    p.add_argument("--clear", action="store_true", help="choose none")
    p.add_argument("--json", action="store_true", help="answer in JSON")
    p.add_argument("--root", help="project root (default: found from the current folder)")
    p.set_defaults(func=cmd_use)
    p = sub.add_parser("check", parents=[common()],
                       help="check printsect.yaml (and .local.yaml) for every tool")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("plugins", parents=[common()], help="the tools and each role's plugins")
    p.set_defaults(func=cmd_plugins)

    deps_cli.configure(
        sub.add_parser("deps", parents=[common()],
                       help="what each output is built from (reads the Makefile)"),
        marks=_probe_marks, name="psect deps", map_data=_page_map,
        mask_names=_knowledge_names)
    probe = sub.add_parser("probe", help="put in, list and take out probes")
    probe_cli.configure(probe.add_subparsers(dest="action", required=True),
                        after_add=[_warn_probe_in_untraced_header])
    deploy_cli.configure(
        sub.add_parser("deploy", parents=[common()], help="put the build on the target (deploy:)"),
        console=_console_link)
    console = sub.add_parser("console", help="read the target's console, send it commands")
    console_cli.configure(console.add_subparsers(dest="action", required=True))
    map_cli.configure(
        sub.add_parser("map", parents=[common()],
                       help="which module uses which (reads the source, runs nothing)"),
        modules=_archive_modules, marks=_probe_marks, outputs=_output_modules,
        builds=_map_builds, rules=_knowledge_rules)
    knowledge_cli.configure(
        sub.add_parser("knowledge", parents=[common()],
                       help="what is known about this build, read as data from your files"))
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser(), argv)


if __name__ == "__main__":
    sys.exit(main())
