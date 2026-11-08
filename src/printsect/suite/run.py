"""One turn of the loop: build, put on the target, see it start, reproduce, read, count.

    1  check the probes against the source
    2  number the run and put the run banner in (the line printing [PSECT-RUN n])
    3  pick the outputs to rebuild: those holding a probed file (deps), or as configured
    4  build
    5  note where the console is, then deploy each output; wait for the banner
    6  reproduce: send commands, wait for lines, pause, or hand a step to a person
    7  read the console until a stop condition
    8  count each probe's hits, write them into the ledger and .printsect/runs/<n>/

A step a person has to do stops the run with WAITING_USER and saves where it was;
`resume` carries on from there, so an agent can hand that step over and come back.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..console import Console, Line, make_console, wait_for
from ..core.config import Project, Schema
from ..core.errors import ConfigError, PsectError
from ..core.fsutil import ensure_state_dir, write_atomic
from ..core.timeutil import now
from ..deploy import FAILED, WAITING_USER, DeployContext, make_deployer
from ..probe.hits import Hit, count
from ..probe.ledger import Ledger
from ..probe.ops import BANNER_GROUP, record_results, set_banner, verify
from .build import execute, outdated, plan

OK = "ok"

SECTIONS: Schema = {
    "build": {"command": (str, list), "steps": list, "cwd": str, "timeout": (int, float),
              "artifact": str, "artifacts": (str, list), "artifact_path": str,
              "check_outputs": bool},
    "run": {"banner": str, "banner_text": str, "ready": str, "ready_timeout": (int, float)},
    "reproduce": list,
    "stop": {"match": (str, list), "quiet": (int, float), "timeout": (int, float),
             "all_hit": bool},
}


@dataclass
class RunResult:
    run: int
    status: str  # ok | failed | waiting_user
    stage: str  # where it ended: build, deploy, ready, reproduce, capture, done
    device: str | None = None
    message: str = ""
    artifacts: list[str] = field(default_factory=list)
    hits: dict[int, Hit] = field(default_factory=dict)
    not_reached: list[int] = field(default_factory=list)
    lines: int = 0
    missed: int = 0
    stopped_by: str = ""
    banner_seen: bool | None = None  # None: no banner set up
    warnings: list[str] = field(default_factory=list)
    folder: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["hits"] = {str(n): h.to_dict() for n, h in self.hits.items()}
        return data


@dataclass
class Hooks:
    """How the run reaches people and the screen."""

    ask: Callable[[str], str | None] | None = None  # None: nobody to ask (an agent, CI)
    log: Callable[[str], None] = print


# -- run numbers and saved state ---------------------------------------------------------


def runs_dir(project: Project) -> Path:
    return project.state_dir / "runs"


def take_run_number(project: Project) -> int:
    folder = runs_dir(project)
    ensure_state_dir(project.state_dir)
    folder.mkdir(exist_ok=True)
    taken = [int(p.name) for p in folder.iterdir() if p.is_dir() and p.name.isdigit()]
    n = max(taken, default=0) + 1
    (folder / str(n)).mkdir()
    return n


def last_run(project: Project) -> int | None:
    folder = runs_dir(project)
    if not folder.is_dir():
        return None
    taken = [int(p.name) for p in folder.iterdir() if p.is_dir() and p.name.isdigit()]
    return max(taken, default=None)


def _save_state(folder: Path, state: dict[str, Any]) -> None:
    write_atomic(folder / "state.json", json.dumps(state, indent=2) + "\n")


# -- the steps ---------------------------------------------------------------------------


def _quote(text: str) -> str:
    return subprocess.list2cmdline([text]) if os.name == "nt" else shlex.quote(text)


def _reproduce(console: Console | None, steps: list[Any], start: int, hooks: Hooks,
               since: int) -> tuple[int, str | None]:
    """Run steps from `start`. Returns (next step, message) — a message when a person
    has to do a step and nobody can be asked here."""
    for i in range(start, len(steps)):
        step = steps[i]
        if not isinstance(step, dict) or len(step) != 1:
            raise ConfigError(f"reproduce[{i}]: one of send, wait, sleep, ask, e.g. "
                              "{send: app_start}")
        (kind, value), = step.items()
        if kind in ("send", "wait") and console is None:
            raise ConfigError(f"reproduce[{i}]: {kind} needs a console (console:)")
        if kind == "send":
            hooks.log(f"send: {value}")
            assert console is not None
            console.send(str(value))
        elif kind == "wait":
            pattern, timeout = (value, 60.0) if isinstance(value, str) else (
                value.get("pattern", ""), float(value.get("timeout", 60)))
            assert console is not None
            if wait_for(console, str(pattern), timeout, since=since) is None:
                raise PsectError(f"reproduce[{i}]: {pattern!r} did not show within {timeout:g}s")
        elif kind == "sleep":
            time.sleep(float(value))
        elif kind == "ask":
            if hooks.ask is None:
                return i + 1, str(value)
            hooks.ask(f"{value} (press Enter when done)")
        else:
            raise ConfigError(f"reproduce[{i}]: {kind!r} is not a step (send, wait, sleep, ask)")
    return len(steps), None


def _capture(project: Project, console: Console, since: int, probes: set[int],
             hooks: Hooks) -> tuple[list[Line], str]:
    stop = project.settings.get("stop") or {}
    match = stop.get("match") or []
    patterns = [re.compile(m) for m in ([match] if isinstance(match, str) else match)]
    quiet = float(stop.get("quiet", 5))
    timeout = float(stop.get("timeout", 120))
    all_hit = bool(stop.get("all_hit", False))
    lines: list[Line] = []
    cursor = since
    start = last_new = time.monotonic()
    seen: set[int] = set()
    tag_re = re.compile(rf"\[{re.escape(project.prefix)}(?:-AI)? (\d+)")
    hooks.log(f"reading the console (stops on {', '.join(match) if patterns else 'quiet'}, "
              f"{quiet:g}s quiet, or {timeout:g}s)")
    while True:
        new = console.read(cursor)
        if new:
            last_new = time.monotonic()
            cursor = new[-1].no
            lines += new
            for ln in new:
                seen.update(int(n) for n in tag_re.findall(ln.text))
                if any(p.search(ln.text) for p in patterns):
                    return lines, f"match: {ln.text.strip()}"
            if all_hit and probes and probes <= seen:
                return lines, "every probe hit"
        t = time.monotonic()
        if t - start >= timeout:
            return lines, f"timeout {timeout:g}s"
        if t - last_new >= quiet and lines:
            return lines, f"quiet {quiet:g}s"
        if t - last_new >= quiet and t - start >= quiet * 2:
            return lines, f"no output for {t - start:.0f}s"
        time.sleep(0.2)


# -- the run -----------------------------------------------------------------------------


def run(project: Project, hooks: Hooks, *, build: bool = True, deploy: bool = True,
        resume: bool = False) -> RunResult:
    project.require_device()
    if resume:
        return _resume(project, hooks)
    report = verify(project)
    warnings = [f"#{n} is no longer in the source" for n in report.missing]
    n = take_run_number(project)
    folder = runs_dir(project) / str(n)
    result = RunResult(run=n, status=OK, stage="build", device=project.device,
                       warnings=warnings, folder=str(folder))

    run_settings = project.settings.get("run") or {}
    if run_settings.get("banner"):
        banner = set_banner(project, n, str(run_settings["banner"]),
                            run_settings.get("banner_text"))
        hooks.log(f"run {n}: banner #{banner.n} at {banner.file}:{banner.line_hint}")
    else:
        result.warnings.append("no run.banner: output from an old build cannot be told apart")
    planned = plan(project, n, hooks.log)
    result.artifacts = planned.artifacts

    if build:
        started = time.time()
        failed = execute(project, planned.steps, folder / "build.log", hooks.log)
        checks = (project.settings.get("build") or {}).get("check_outputs", True)
        if not failed and planned.steps and checks:
            old = outdated(project, planned.artifacts, started)
            if old:
                failed = ("the build said it passed, but these were not made anew: "
                          + ", ".join(old) + ". If the build hands the work to another tool, "
                          "wait for it in build.steps; check build.artifact_path")
        if failed:
            result.status, result.message = FAILED, failed
            return _finish(project, result)

    console = _open_console(project)
    try:
        since = console.mark() if console else 0
        state = {"run": n, "since": since, "step": 0, "artifacts": result.artifacts,
                 "warnings": result.warnings}
        _save_state(folder, state)
        if deploy:
            result.stage = "deploy"
            message = _deploy(project, n, result.artifacts, console, since, hooks)
            if message is not None:
                status, text = message
                result.status, result.message = status, text
                if status == WAITING_USER:
                    _save_state(folder, {**state, "stage": "deploy"})
                return _finish(project, result)
            not_ready = _wait_ready(project, console, since, n, hooks)
            if not_ready:
                result.stage, result.status, result.message = "ready", FAILED, not_ready
                return _finish(project, result)
        return _after_deploy(project, hooks, result, console, state)
    finally:
        if console:
            console.close()


def _resume(project: Project, hooks: Hooks) -> RunResult:
    n = last_run(project)
    folder = runs_dir(project) / str(n) if n else None
    if folder is None or not (folder / "state.json").is_file():
        raise PsectError("there is no stopped run to carry on")
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    if state.get("stage") not in ("deploy", "reproduce"):
        raise PsectError(f"run {n} is not waiting for anything")
    result = RunResult(run=int(state["run"]), status=OK, stage="reproduce",
                       artifacts=list(state["artifacts"]), warnings=list(state["warnings"]),
                       folder=str(folder))
    hooks.log(f"run {n}: carrying on")
    console = _open_console(project)
    try:
        if state.get("stage") == "deploy":  # a person deployed it: see it come up first
            not_ready = _wait_ready(project, console, int(state["since"]), int(state["run"]),
                                    hooks)
            if not_ready:
                result.stage, result.status, result.message = "ready", FAILED, not_ready
                return _finish(project, result)
        return _after_deploy(project, hooks, result, console, state)
    finally:
        if console:
            console.close()


def _wait_ready(project: Project, console: Console | None, since: int, run: int,
                hooks: Hooks) -> str | None:
    """Wait for run.ready (e.g. the prompt) after this run's banner; None when it came.

    The banner says the new build started; ready says it can take commands, so the
    steps that reproduce the problem are not sent into a target still starting up.
    """
    settings = project.settings.get("run") or {}
    ready = settings.get("ready")
    if not ready or console is None:
        return None
    timeout = float(settings.get("ready_timeout", 120))
    start = since
    banner = re.compile(rf"\[{re.escape(project.prefix)}-RUN {run}\]")
    for line in console.read(since):  # look after this build's banner, not an old prompt
        if banner.search(line.text):
            start = line.no
    hooks.log(f"waiting for the target to be ready ({ready})")
    if wait_for(console, str(ready), timeout, since=start) is None:
        return (f"the target did not show {ready!r} within {timeout:g}s after starting "
                "(run.ready); is it still booting, or did it stop?")
    return None


def _open_console(project: Project) -> Console | None:
    settings = project.settings.get("console") or {}
    return make_console(settings) if settings.get("type") else None


def _deploy(project: Project, n: int, artifacts: list[str], console: Console | None,
            since: int, hooks: Hooks) -> tuple[str, str] | None:
    settings = project.settings.get("deploy") or {}

    def watch(pattern: str, timeout: float) -> str | None:
        assert console is not None
        line = wait_for(console, pattern, timeout, since=since)  # from before the deploy
        return line.text if line else None

    for artifact in artifacts or [""]:  # "": a program on this PC, nothing to put anywhere
        ctx = DeployContext(
            root=project.root, run=n, prefix=project.prefix, device=project.device or "",
            artifact=project.root / artifact if artifact else None,
            ask=hooks.ask, wait_for=watch if console else None, log=hooks.log,
            send=console.send if console else None,
        )
        done = make_deployer(settings).deploy(ctx)
        hooks.log(f"deploy {artifact or ''}: {done.status}: {done.message}")
        if done.status != OK:
            return done.status, done.message
    return None


def _after_deploy(project: Project, hooks: Hooks, result: RunResult, console: Console | None,
                  state: dict[str, Any]) -> RunResult:
    folder = Path(result.folder)
    since = int(state["since"])
    steps = project.settings.get("reproduce") or []
    result.stage = "reproduce"
    next_step, ask = _reproduce(console, steps, int(state.get("step", 0)), hooks, since)
    if ask is not None:
        _save_state(folder, {**state, "stage": "reproduce", "step": next_step})
        result.status = WAITING_USER
        result.message = f"{ask} — then carry on with: psect run --resume"
        return _finish(project, result)
    _save_state(folder, {**state, "stage": "capture", "step": next_step})

    if console is None:
        result.stage = "done"
        result.warnings.append("no console (console:), so nothing was read; count hits from "
                               "a saved log with psect probe hits")
        return _finish(project, result)
    result.stage = "capture"
    probes = {p.n for p in Ledger(project.state_dir).active() if p.group != BANNER_GROUP}
    lines, why = _capture(project, console, since, probes, hooks)
    result.lines, result.stopped_by, result.missed = len(lines), why, console.missed
    write_atomic(folder / "console.log", "".join(f"{ln.no}\t{ln.text}\n" for ln in lines))

    if (project.settings.get("run") or {}).get("banner"):
        mine = re.compile(rf"\[{re.escape(project.prefix)}-RUN {result.run}\]")
        result.banner_seen = any(mine.search(ln.text) for ln in lines)
        if not result.banner_seen:
            result.warnings.append(f"[{project.prefix}-RUN {result.run}] never showed: the "
                                   "output may be from an old build")
    if result.missed:
        result.warnings.append(f"{result.missed} console line(s) went by unread; hits may be "
                               "undercounted (read more lines per request)")
    result.hits = count(((ln.no, ln.text) for ln in lines), project.prefix)
    record_results(project, result.run, result.hits)
    result.not_reached = sorted(probes - set(result.hits))
    result.stage = "done"
    return _finish(project, result)


def _finish(project: Project, result: RunResult) -> RunResult:
    folder = Path(result.folder)
    data = {**result.to_dict(), "at": now()}
    write_atomic(folder / "summary.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return result
