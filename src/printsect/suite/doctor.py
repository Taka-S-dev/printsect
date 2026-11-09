"""`psect doctor`: is this project set up for `psect run`? Each part tried for real.

It reads the settings, reads the build (make -n), reaches the console, looks at the
line chosen for the run banner, and looks for the build and deploy commands. It does
not build, deploy, send anything to the target, or change a file, and what it finds
stays on this PC. Every finding that is not ok comes with what to do about it.
"""

from __future__ import annotations

import shlex
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from ..core.config import CORE_SCHEMA, Project, Schema, check, problems
from ..core.errors import ConfigError, PsectError

OK, WARN, NG, SKIP = "ok", "warn", "ng", "--"


@dataclass
class Finding:
    name: str
    status: str
    message: str
    hint: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


# -- the settings, for every tool ----------------------------------------------------------


def all_sections() -> Schema:
    from .. import console, deploy, deps, knowledge, map, probe
    from .run import SECTIONS

    return {**CORE_SCHEMA, **console.SECTIONS, **deps.SECTIONS, **probe.SECTIONS,
            **deploy.SECTIONS, **map.SECTIONS, **knowledge.SECTIONS, **SECTIONS}


def _check_console(settings: Any) -> object:
    """The console's settings, checked without opening it (a port is opened on use)."""
    from ..core import plugins

    settings = dict(settings or {})
    kind = settings.pop("type", None)
    if kind:
        schema = getattr(plugins.load("console", str(kind)), "SETTINGS", None)
        if schema is not None:
            check(settings, schema, "console")
    return None


def settings_problems(project: Project) -> list[str]:
    """Every unknown or mistyped key, in every section and the plugins' sections, for
    the settings as they are and as each device's part of `devices:` makes them."""
    from ..core.config import load_project

    found = _problems(project)
    for name in project.devices:
        if name == project.device:
            continue
        try:
            other = load_project(project.root, device=name)
        except ConfigError as exc:
            found.append(f"devices.{name}: {exc}")
            continue
        found += [f"devices.{name}: {p}" for p in _problems(other) if p not in found]
    return found


def _problems(project: Project) -> list[str]:
    from .. import deploy, deps, map

    plugin_sections: dict[str, Callable[[Any], object]] = {
        "deps": deps.make_scanner,
        "deploy": deploy.make_deployer,
        "console": _check_console,
        "map": map.make_indexer,
    }
    top = {k: (v if k not in plugin_sections or not isinstance(v, dict)
               else {"type": v.get("type")} if "type" in v else {})
           for k, v in project.settings.items()}
    found = problems(top, all_sections())
    for section, make in plugin_sections.items():
        try:
            make(project.settings.get(section))
        except ConfigError as exc:
            found += [line.strip() for line in str(exc).splitlines()[1:]] or [str(exc)]
    return found


# -- each part ---------------------------------------------------------------------------


def _program(command: Any) -> str | None:
    """The program a command starts, when it can be told (for a PATH lookup)."""
    if isinstance(command, list):
        return str(command[0]) if command else None
    try:
        words = shlex.split(str(command), posix=False)
    except ValueError:
        return None
    return words[0].strip('"') if words else None


def _found(program: str | None) -> bool:
    return program is None or shutil.which(program) is not None


def _settings(project: Project) -> Finding:
    found = settings_problems(project)
    if found:
        return Finding("settings", NG, f"{len(found)} problem(s): " + "; ".join(found[:3]),
                       "psect check lists them all, each with the nearest real key")
    return Finding("settings", OK, "printsect.yaml and printsect.local.yaml read without errors")


def _imported(project: Project) -> tuple[Finding, Any] | None:
    """A build read from a graph file is checked as it is: make cannot read it again, and
    trying would replace it."""
    from ..deps import load
    from ..deps.graph import IMPORTED

    if not project.state_file("deps.json").is_file():
        return None
    try:
        graph = load(project)
    except PsectError:
        return None
    if graph.command != IMPORTED:
        return None
    changed = graph.stale_inputs(project.root)
    if changed:
        return Finding("deps", WARN, f"{', '.join(changed[:3])} changed since the graph file "
                       "was read", "write it again, then psect deps --from-graph FILE"), graph
    return Finding("deps", OK, f"read from a graph file: {len(graph.final_outs())} output(s), "
                   f"{len(graph.archives)} archive(s), {len(graph.objects)} object(s)"), graph


def _deps(project: Project, run_commands: bool = True) -> tuple[Finding, Any]:
    from ..deps import load, make_scanner, save

    settings = project.settings.get("deps") or {}
    auto = (project.settings.get("build") or {}).get("artifacts") == "auto"
    imported = _imported(project)
    if imported:
        return imported
    if not run_commands:  # make -n runs $(shell ...) in Makefiles: not before approval
        held = project.state_file("deps.json").is_file()
        return Finding("deps", WARN, f"not run for an agent: reading the build "
                       f"({settings.get('command') or 'make -n -B -w'}) is not approved yet",
                       "the person checks it and runs psect approve (or psect doctor)"), (
            load(project) if held else None)
    try:
        graph = make_scanner(settings).scan(project)
    except PsectError as exc:
        status = NG if auto else WARN
        why = " ".join(line.strip() for line in str(exc).splitlines()[:2])  # with make's own
        return Finding("deps", status, f"could not read the build: {why}",
                       "set deps.command to what prints your build (make -n -B -w, with the "
                       "-f/-C/target you use) and deps.cwd to where it runs"), None
    save(project, graph)
    compiles = len([o for o in graph.objects.values() if o.get("source")])
    summary = (f"read the build: {len(graph.final_outs())} output(s), {len(graph.archives)} "
               f"archive(s), {compiles} source(s), {len(graph.headers())} header(s)")
    if compiles == 0:
        return Finding("deps", NG if auto else WARN, summary + "; no compile commands seen",
                       "the build printed no `-c` compiles: check deps.command prints commands "
                       "(make -n) and runs in the right folder (deps.cwd)"), graph
    if not graph.final_outs():
        return Finding("deps", WARN, summary + "; no link step seen",
                       "outputs come from link steps (-o with objects or archives); if your "
                       "build links differently, set build.artifacts as a list"), graph
    return Finding("deps", OK, summary), graph


def _console(project: Project, run_commands: bool = True) -> Finding:
    from ..console import make_console

    settings = project.settings.get("console") or {}
    kind = settings.get("type")
    if kind == "command" and not run_commands:
        return Finding("console", WARN, "not run for an agent: console.read is a command "
                       "not approved yet", "the person checks it and runs psect approve")
    if not kind:
        return Finding("console", WARN, "no console set up: runs cannot read the target",
                       "add console: {type: tcp|command|serial|file} to printsect.local.yaml "
                       "(psect init wrote examples there)")
    hints = {
        "tcp": "is the program holding the port running, and on console.port?",
        "command": "does console.read print the lines as JSON when run by hand?",
        "serial": "is the port right, and free (no other program holding it)?",
        "file": "does console.path name the log the terminal program writes?",
    }
    try:
        console = make_console(settings)
        try:
            last = console.mark()
        finally:
            console.close()
    except (PsectError, OSError) as exc:
        return Finding("console", NG, f"{kind}: {str(exc).splitlines()[0]}",
                       hints.get(str(kind), "check the console settings"))
    return Finding("console", OK, f"{kind}: reached, last line {last}")


def _banner(project: Project, graph: Any) -> Finding:
    from ..probe.cscan import enclosing_function, ends_statement
    from ..probe.textfile import TextFile, body

    where = (project.settings.get("run") or {}).get("banner")
    if not where:
        return Finding("banner", WARN, "no run.banner: output from an old build still on the "
                       "target cannot be told from the new one",
                       "set run.banner: FILE:LINE, a line in code every new build runs once "
                       "when it starts (its init function); the banner goes below it")
    file, _, line_text = str(where).rpartition(":")
    if not file or not line_text.isdigit():
        return Finding("banner", NG, f"run.banner is {where!r}, not FILE:LINE",
                       "e.g. app/init.c:42, relative to the project root")
    line = int(line_text)
    try:
        path = project.abs(file)
        tf = TextFile.read(path, project.encodings)
    except (PsectError, OSError) as exc:
        return Finding("banner", NG, f"cannot read {file}: {str(exc).splitlines()[0]}",
                       "FILE is relative to the project root")
    if not 1 <= line <= len(tf.lines):
        return Finding("banner", NG, f"{file} has {len(tf.lines)} lines; no line {line}",
                       "LINE is the line the banner goes below")
    bodies = [body(x) for x in tf.lines]
    function = enclosing_function(bodies, line)
    if function is None:
        return Finding("banner", NG, f"{file}:{line} is not inside a function",
                       "pick a line inside the function that runs when the build starts")
    notes = []
    if not ends_statement(bodies[line - 1]):
        notes.append(f"line {line} does not end a statement")
    if graph is not None and not graph.roots_for_file(project.rel(path)):
        notes.append(f"{file} is not in any output the build makes")
    message = f"{file}:{line} is in {function}()"
    if notes:
        return Finding("banner", WARN, message + "; " + "; ".join(notes),
                       "the banner line goes below LINE: choose the end of a statement in "
                       "code the outputs you deploy contain")
    return Finding("banner", OK, message)


def _build(project: Project, graph: Any, run_commands: bool = True) -> Finding:
    from ..probe.ledger import Ledger
    from .build import plan

    build = project.settings.get("build") or {}
    if not build.get("command") and not build.get("steps"):
        return Finding("build", NG, "no build.steps or build.command",
                       "rules per archive and output (build.steps, e.g. for: archives, run: "
                       "make -C {made_in}), or one command with {outs}, e.g. make {outs}")
    if build.get("artifacts") == "auto" and graph is None:
        return Finding("build", NG, "build.artifacts is auto but the build could not be read",
                       "fix deps first, or list the outputs in build.artifacts")
    try:
        quiet = lambda text: None  # noqa: E731
        planned = plan(project, 0, quiet, may_scan=run_commands)
        if not planned.files and graph is not None:  # no probes yet: show the whole build
            planned = plan(project, 0, quiet, everything=True, may_scan=run_commands)
    except (ConfigError, PsectError) as exc:
        return Finding("build", NG, str(exc).splitlines()[0],
                       "psect build --plan shows what would run")
    missing = sorted({prog for prog in (_program(st.args) for st in planned.steps)
                      if prog and not _found(prog)})
    if missing:
        return Finding("build", WARN, f"{', '.join(missing)} not on PATH here",
                       "fine if the shell finds it some other way; else give a full path")
    if not planned.outs:
        return Finding("build", WARN, "no outputs to deploy (build.artifacts / artifact)",
                       "artifacts: auto picks the outputs holding a probed file")
    probed = Ledger(project.state_dir).active()
    what = (f"{len(planned.files)} probed file(s)" if probed
            else "every source (no probes in yet)")
    return Finding("build", OK, f"{len(planned.steps)} command(s) for {what}, "
                   f"{len(planned.outs)} output(s) to deploy; not run by doctor "
                   "(psect build --plan --all lists them)")


def _deploy(project: Project, has_console: bool, run_commands: bool = True) -> Finding:
    from ..deploy import make_deployer

    settings = project.settings.get("deploy") or {}
    if "deploy" not in project.settings or "type" not in settings:
        return Finding("deploy", WARN, "no deploy set up: nothing will put a build on the "
                       "target", "ask how a build gets onto the target; deploy: {type: "
                       "manual} for a person's own tool, command for commands here, none "
                       "for a program run on this PC")
    kind = settings["type"]
    try:
        deployer = make_deployer(settings)
    except ConfigError as exc:
        return Finding("deploy", NG, str(exc).splitlines()[-1].strip(),
                       "see the deploy tool's guide for its settings")
    check = getattr(deployer, "check", None)
    if callable(check):
        if not run_commands:  # it reaches the target: not before approval
            return Finding("deploy", WARN, f"{kind}: not tried, the settings are not approved",
                           "the person runs psect approve, then doctor tries it")
        try:
            reached = str(check(project.root))
        except ConfigError as exc:
            return Finding("deploy", NG, str(exc).splitlines()[-1].strip(),
                           "check the address, the user and the password variable; "
                           "a person sets the password, never in a settings file")
        if settings.get("after") and not has_console:
            return Finding("deploy", WARN, f"{kind}: {reached}; no console to send after: to",
                           "set up console: (one that sends), or a person types them")
        return Finding("deploy", OK, f"{kind}: {reached}")
    if kind == "command":
        commands = settings.get("commands") or [settings.get("command")]
        missing = [p for p in (_program(c) for c in commands if c) if p and not _found(p)]
        if missing:
            return Finding("deploy", WARN, f"{', '.join(missing)} not on PATH here",
                           "fine if the shell finds it some other way; else give a full path")
    if kind in ("manual", "command") and not has_console:
        return Finding("deploy", WARN, f"{kind}: no console, so the new build cannot be seen "
                       "starting", "set up console: so the run banner confirms the deploy")
    note = {"manual": ": a person transfers; the run banner on the console confirms it",
            "none": ": nothing to put anywhere (a program run on this PC)"}.get(kind, "")
    return Finding("deploy", OK, f"{kind}{note}")


def _reproduce(project: Project, has_console: bool) -> Finding:
    steps = project.settings.get("reproduce") or []
    if not steps:
        return Finding("reproduce", SKIP, "no steps: a run only reads what the target prints")
    kinds = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or len(step) != 1:
            return Finding("reproduce", NG, f"reproduce[{i}] is not one step",
                           "each step is one of {send: CMD}, {wait: PATTERN}, {sleep: SEC}, "
                           "{ask: TEXT}")
        (kind,) = step
        if kind not in ("send", "wait", "sleep", "ask"):
            return Finding("reproduce", NG, f"reproduce[{i}]: {kind!r} is not a step",
                           "steps are send, wait, sleep and ask")
        kinds.append(kind)
    if not has_console and {"send", "wait"} & set(kinds):
        return Finding("reproduce", NG, "send and wait need a console", "set up console:")
    if "send" in kinds and not _can_send(project.settings.get("console") or {}):
        return Finding("reproduce", NG, "send: this console only reads",
                       "a file console cannot send; use tcp or command with send set, or "
                       "send from the terminal program by hand (an ask step)")
    return Finding("reproduce", OK, f"{len(steps)} step(s): {', '.join(kinds)}")


def _can_send(console: dict[str, Any]) -> bool:
    kind = console.get("type")
    if kind == "file":
        return False
    if kind == "command":
        return bool(console.get("send"))
    if kind == "tcp":
        return bool(console.get("send_request"))
    return True  # serial, or a plugin: assume it can


def _probes(project: Project) -> Finding:
    from ..probe.ops import BANNER_GROUP, verify

    report = verify(project)
    mine = [f for f in report.found if f.mark.ai and f.mark.group != BANNER_GROUP]
    notes = []
    if report.missing:
        notes.append(f"{len(report.missing)} gone from the source")
    if report.orphans:
        notes.append(f"{len(report.orphans)} agent mark(s) not in the list")
    if notes:
        return Finding("probes", WARN, f"{len(mine)} in the source; " + "; ".join(notes),
                       "psect probe list shows them; psect probe clear --orphans tidies up")
    return Finding("probes", OK, f"{len(mine)} in the source")


def _device(project: Project) -> Finding:
    if not project.devices:
        return Finding("device", SKIP, "no devices: set up; one set of settings for all")
    if project.device is None:
        return Finding("device", NG, f"none chosen of {', '.join(project.devices)}",
                       "psect use NAME, so builds and deploys are for the right device")
    others = [d for d in project.devices if d != project.device]
    return Finding("device", OK, f"{project.device}"
                   + (f" (also set up: {', '.join(others)})" if others else ""))


def _approval(project: Project) -> Finding:
    from .approve import commands, unapproved

    listed = commands(project)
    if not listed:
        return Finding("approval", SKIP, "no commands in the settings besides make -n")
    waiting = unapproved(project)
    if waiting:
        return Finding("approval", WARN, f"{len(waiting)} command(s) in the settings not "
                       "approved: an agent cannot run them yet",
                       "the person reads them with psect approve --show, then psect approve")
    return Finding("approval", OK, f"{len(listed)} command(s) in the settings approved")


def diagnose(project: Project, run_commands: bool = True) -> list[Finding]:
    """Every part, tried for real. run_commands=False (for an agent): commands from the
    settings that a person has not approved are named, not run."""
    if not run_commands:
        from .approve import unapproved

        run_commands = not unapproved(project)
    found = [_device(project), _settings(project), _approval(project)]
    deps, graph = _deps(project, run_commands)
    found.append(deps)
    console = _console(project, run_commands)
    found.append(console)
    has_console = console.status == OK
    found += [_banner(project, graph), _build(project, graph, run_commands),
              _deploy(project, has_console, run_commands), _reproduce(project, has_console),
              _probes(project)]
    return found
