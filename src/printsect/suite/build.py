"""The build for the probes in: what to rebuild, in what order, with which commands.

The commands come from rules, not from a list kept by hand: printsect knows from the
dependency graph which archives hold a probed file and which outputs hold those, and
fills each rule in once per archive or output, in the order the build makes them.

    build:
      steps:
        - run: make clean          # once, where it stands: never forgotten
        - for: archives            # each archive holding a probed file
          run: make -C {made_in}
        - for: outs                # then each output holding one of them
          run: make {path}

`build.command` (one command, {outs} = the outputs) still works for a build that is
one command. `psect build --plan` shows the commands without running them.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import Project
from ..core.errors import ConfigError, PsectError
from ..core.fsutil import write_atomic
from ..deps.graph import IMPORTED, Graph
from ..probe.ledger import Ledger

STEP_KINDS = ("archives", "outs", "once")


@dataclass
class Step:
    label: str  # what it builds: "archive lib/libnet.a", "out app.out", "once"
    args: str | list[str]
    shell: bool

    def text(self) -> str:
        return self.args if isinstance(self.args, str) else " ".join(self.args)


@dataclass
class Plan:
    files: list[str] = field(default_factory=list)  # the probed files it is for
    archives: list[str] = field(default_factory=list)  # to rebuild, in build order
    outs: list[str] = field(default_factory=list)  # to rebuild, as the build names them
    artifacts: list[str] = field(default_factory=list)  # the files to deploy (artifact_path)
    steps: list[Step] = field(default_factory=list)
    device: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"device": self.device, "files": self.files, "archives": self.archives,
                "outs": self.outs, "artifacts": self.artifacts,
                "steps": [{"for": s.label, "command": s.text()} for s in self.steps]}


def _quote(text: str) -> str:
    return subprocess.list2cmdline([text]) if os.name == "nt" else shlex.quote(text)


def _fill(text: str, values: dict[str, str]) -> str:
    for k, v in values.items():
        text = text.replace("{" + k + "}", v)
    return text


_SAFE_VALUE = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./\\+@-]*")  # used with fullmatch


_NOT_INTO = {".git", ".svn", ".hg", ".printsect"}


def _safe(value: str, what: str) -> str:
    """A value from the dependency graph, before it goes into a command. The graph lives
    in .printsect/, which approval does not cover, so a value there must not be able to
    add to a command or point the build anywhere approval does not look: a path relative
    to the project, of plain characters, with no leading dash, no drive or root, no
    `..`, and not into version control or printsect's state."""
    # Folder names compared as the file system will see them: any case, and Windows
    # drops trailing dots and spaces (".GIT." is .git there).
    parts = [p.rstrip(". ").casefold() or p for p in value.replace("\\", "/").split("/")]
    if (not _SAFE_VALUE.fullmatch(value) or ".." in value.replace("\\", "/").split("/")
            or any(p in _NOT_INTO for p in parts)):
        raise PsectError(f"{what} {value!r} is not a plain path inside the project; "
                         "printsect does not put it into a command (read the build again: "
                         "psect deps)")
    return value


def _names(path: str, made_in: str) -> dict[str, str]:
    """The placeholders for one archive or output:

    {path} build/lib/libnet.a   {name} libnet.a   {dir} build/lib   {stem} libnet
    {module} net (lib taken off)   {made_in} net, the folder the build made it in
    """
    path = _safe(path, "output")
    made_in = _safe(made_in or ".", "folder")
    name = path.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    return {"path": path, "name": name, "dir": path.rsplit("/", 1)[0] if "/" in path else ".",
            "stem": stem, "module": stem[3:] if stem.startswith("lib") else stem,
            "made_in": made_in or "."}


def load_graph(project: Project, log: Callable[[str], None], may_scan: bool = True) -> Graph:
    """The dependency graph, read again when a Makefile or the settings changed.
    With may_scan=False it never runs make: a graph already read and current, or an error."""
    from ..deps import DEPS_FILE, load, make_scanner, runs_unasked, save

    settings = project.settings.get("deps")
    if project.state_file(DEPS_FILE).is_file():
        graph = load(project)
        if not graph.stale_inputs(project.root):
            return graph
        changed = ", ".join(graph.stale_inputs(project.root)[:3])
        if graph.command == IMPORTED:  # make cannot read this build again
            raise PsectError(f"{changed} changed since the graph file was read: write it "
                             "again, then psect deps --from-graph FILE")
        log(f"note: {changed} changed since the build was read; reading it again")
    if not may_scan:
        raise PsectError("the build would have to be read (make -n), which is not allowed here")
    if not runs_unasked(settings):
        raise PsectError("the build has not been read yet: run psect deps first")
    graph = make_scanner(settings).scan(project)
    save(project, graph)
    return graph


def affected(graph: Graph, files: list[str]) -> tuple[list[str], list[str]]:
    """The archives and outputs holding any of `files`, each in the order it is built."""
    up = graph.parents()
    reached: set[str] = set()
    stack = [node for f in files for node, _ in graph.starts_for(f)]
    while stack:
        node = stack.pop()
        for parent in up.get(node, []):
            if parent not in reached:
                reached.add(parent)
                stack.append(parent)
    finals = set(graph.final_outs())
    archives = [a for a in graph.archives
                if a in reached and not graph.archives[a].get("prebuilt")]
    outs = [o for o in graph.outs if o in reached and o in finals]
    return archives, outs


def probed_files(project: Project) -> list[str]:
    """The files holding a probe now, and the file the run banner goes into."""
    files = {p.file for p in Ledger(project.state_dir).active()}
    banner = (project.settings.get("run") or {}).get("banner")
    if banner and ":" in str(banner):
        files.add(str(banner).rpartition(":")[0])
    return sorted(files)


def plan(project: Project, run: int, log: Callable[[str], None] = print, *,
         files: list[str] | None = None, everything: bool = False,
         may_scan: bool = True) -> Plan:
    """The build for the probes in now; or for `files`; or, with `everything`, as if every
    source were probed (to see and agree the commands before any probe is in)."""
    build = project.settings.get("build") or {}
    steps_cfg = build.get("steps")
    command = build.get("command")
    if steps_cfg and command:
        raise ConfigError("build: give steps or command, not both")
    wanted = build.get("artifacts")
    if wanted is not None and wanted != "auto" and not isinstance(wanted, list):
        raise ConfigError("build.artifacts: a list of outputs, or auto")
    rules = _rules(steps_cfg) if steps_cfg else []
    needs_graph = wanted == "auto" or any(r["for"] != "once" for r in rules)

    result = Plan(files=list(files) if files is not None else probed_files(project),
                  device=project.device)
    made_in: dict[str, str] = {}
    if needs_graph:
        graph = load_graph(project, log, may_scan)
        if everything:
            result.files = sorted(graph.sources())
        made_in = {p: str(info.get("made_in", "")) for p, info in
                   [*graph.archives.items(), *graph.outs.items()]}
        result.archives, result.outs = affected(graph, result.files)
        if result.files and not result.outs:
            raise PsectError("no output of the build holds the probed files: "
                             + ", ".join(result.files))
    if isinstance(wanted, list):
        result.outs = [str(a) for a in wanted]
    elif wanted is None:
        result.outs = [str(build["artifact"])] if build.get("artifact") else []

    common = {"run": str(run), "root": str(project.root), "device": project.device or ""}
    # Where each output is taken from to deploy: as built, or the device's own place.
    where = build.get("artifact_path")
    result.artifacts = [
        _fill(str(where), {**common, **_names(o, made_in.get(o, ""))}) if where else o
        for o in result.outs
    ]
    for rule in rules:
        items = {"archives": result.archives, "outs": result.outs, "once": [""]}[rule["for"]]
        for item in items:
            if rule.get("match") and not fnmatch.fnmatch(item, rule["match"]):
                continue
            values = {**common, **(_names(item, made_in.get(item, "")) if item else {})}
            result.steps.append(_step(rule["run"], values, f"{rule['for'][:-1]} {item}"
                                      if item else "once"))
    if command:
        result.steps.append(_command_step(command, common, result.outs))
    return result


def _rules(steps: Any) -> list[dict[str, Any]]:
    if not isinstance(steps, list):
        raise ConfigError("build.steps: a list of {for: archives|outs|once, run: COMMAND}")
    rules = []
    for i, rule in enumerate(steps):
        if not isinstance(rule, dict) or "run" not in rule:
            raise ConfigError(f"build.steps[{i}]: needs run: COMMAND")
        unknown = set(rule) - {"for", "run", "match"}
        if unknown:
            raise ConfigError(f"build.steps[{i}]: {', '.join(sorted(unknown))} is not a key "
                              "(for, run, match)")
        kind = rule.get("for", "once")
        if kind not in STEP_KINDS:
            raise ConfigError(f"build.steps[{i}].for: one of {', '.join(STEP_KINDS)}")
        if not isinstance(rule["run"], (str, list)):
            raise ConfigError(f"build.steps[{i}].run: a command, as text or a list of words")
        rules.append({**rule, "for": kind})
    return rules


def _step(template: str | list[str], values: dict[str, str], label: str) -> Step:
    if isinstance(template, list):
        return Step(label, [_fill(str(w), values) for w in template], shell=False)
    return Step(label, _fill(template, {k: _quote(v) for k, v in values.items()}), shell=True)


def _command_step(command: str | list[str], values: dict[str, str], outs: list[str]) -> Step:
    if isinstance(command, list):
        args: list[str] = []
        for word in command:
            args += ([_safe(o, "output") for o in outs] if word == "{outs}"
                     else [_fill(str(word), values)])
        return Step("build", args, shell=False)
    safe_outs = " ".join(_quote(_safe(o, "output")) for o in outs)
    return Step("build", _fill(command, {**values, "outs": safe_outs}), shell=True)


def outdated(project: Project, artifacts: list[str], since: float) -> list[str]:
    """The artifacts the build did not make anew: missing, or older than `since`.

    A build command that hands the work to another tool and returns at once looks
    like a success; this is what tells an old output from a new one.
    """
    found = []
    for artifact in artifacts:
        path = project.abs(artifact)
        if not path.is_file():
            found.append(f"{artifact} (not there)")
        elif path.stat().st_mtime < since - 1:  # a second's slack for coarse clocks
            found.append(f"{artifact} (not updated)")
    return found


def execute(project: Project, steps: list[Step], log_file: Path,
            log: Callable[[str], None] = print) -> str | None:
    """Run the steps in order; None when all passed, else what went wrong."""
    build = project.settings.get("build") or {}
    cwd = project.root / str(build.get("cwd", "."))
    timeout = float(build.get("timeout", 1800))
    output = []
    for step in steps:
        log(f"build ({step.label}): {step.text()}")
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}  # no .pyc left for later
        try:
            done = subprocess.run(step.args, shell=step.shell, cwd=cwd, capture_output=True,
                                  timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return f"{step.text()} took over {timeout:g}s"
        except OSError as exc:
            return f"cannot run {step.text()}: {exc}"
        text = (done.stdout + done.stderr).decode("utf-8", errors="replace")
        output.append(f"$ {step.text()}\n{text}")
        write_atomic(log_file, "\n".join(output))
        if done.returncode != 0:
            errors = [ln for ln in text.splitlines() if re.search(r"error|Error|ERROR", ln)]
            shown = "\n".join((errors or text.splitlines())[-15:])
            return (f"the build failed at {step.text()} (exit {done.returncode}); see "
                    f"{log_file}\n{shown}")
    if not steps:
        log("note: no build.steps or build.command; using the outputs as they are")
    return None
