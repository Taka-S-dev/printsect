"""The builtin dependency scanner: ask make what it would run, and read that."""

from __future__ import annotations

import locale
import os
import subprocess
from pathlib import Path
from typing import Any

from ..core.config import CONFIG_NAME, LOCAL_CONFIG_NAME, Project
from ..core.errors import ConfigError, PsectError
from ..core.plugins import register
from ..core.timeutil import now
from .graph import Graph
from .includes import trace
from .makelog import Kinds, read_log
from .paths import to_rel

# -n: print, do not run. -B: as if everything were out of date, so every step shows.
# -w: print "Entering directory", so paths from sub-makes can be placed.
DEFAULT_COMMAND = "make -n -B -w"
WATCH_NAMES = ("Makefile", "makefile", "GNUmakefile")
WATCH_SUFFIXES = (".mk", ".mak")
SKIP_DIRS = {".git", ".svn", ".hg", ".printsect", "node_modules", "__pycache__", ".venv"}


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), errors="replace")


@register("deps", "make")
class MakeScanner:
    SETTINGS = {
        "command": (str, list), "cwd": str, "timeout": (int, float), "headers": bool,
        "source_extensions": list, "object_extensions": list, "archive_extensions": list,
        "ignore_tools": list, "also_ignore": list, "watch_names": list, "watch_suffixes": list,
        "approval_ignore": list,  # files the build writes, left out of approval (approved)
    }

    def __init__(self, settings: dict[str, Any]) -> None:
        self.command = settings.get("command") or DEFAULT_COMMAND
        if not isinstance(self.command, (str, list)):
            raise ConfigError("deps.command must be a string or a list of words")
        self.cwd = str(settings.get("cwd") or ".")
        self.timeout = float(settings.get("timeout") or 300)
        self.kinds = Kinds.from_settings(settings)
        # Following #include finds what a header change rebuilds; the Makefile alone
        # rarely says. Off: only sources -> objects -> archives -> outputs (faster).
        self.headers = bool(settings.get("headers", True))
        self.watch_names = tuple(settings.get("watch_names") or WATCH_NAMES)
        self.watch_suffixes = tuple(settings.get("watch_suffixes") or WATCH_SUFFIXES)

    def scan(self, project: Project, *, log: str | None = None) -> Graph:
        cwd = (project.root / self.cwd).resolve()
        if log is None:
            text = self._run(cwd)
            command = self.command if isinstance(self.command, str) else " ".join(self.command)
        else:
            text, command = log, "(a saved log)"
        root = str(project.root)
        graph, counts = read_log(text, root, str(cwd), self.kinds)
        if self.headers:
            trace(graph, root)
        else:
            graph.headers_traced = False
        graph.generated = now()
        graph.command = command
        graph.inputs = self._watched(project)
        if counts["compile"] == 0:
            graph.warnings.append(
                "no compile commands were found; check that deps.command prints the commands"
                " (make -n) and runs in the right folder (deps.cwd)"
            )
        # -l names the build does not make (libc, libm, ...) stay in outs[...]["libs"].
        return graph

    def _run(self, cwd: Path) -> str:
        env = {**os.environ, "LC_ALL": "C", "LANG": "C", "LANGUAGE": "C"}  # English messages
        try:
            proc = subprocess.run(
                self.command, shell=isinstance(self.command, str), cwd=cwd, env=env,
                capture_output=True, timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise ConfigError(f"cannot run {self.command!r}: {exc}; set deps.command") from exc
        except subprocess.TimeoutExpired as exc:
            raise PsectError(f"{self.command!r} took over {self.timeout:g}s") from exc
        if proc.returncode != 0:
            tail = "\n".join(_decode(proc.stderr).strip().splitlines()[-15:])
            raise PsectError(f"{self.command!r} failed (exit {proc.returncode}):\n{tail}")
        return _decode(proc.stdout)

    def _watched(self, project: Project) -> dict[str, int]:
        found: dict[str, int] = {}
        for name in (CONFIG_NAME, LOCAL_CONFIG_NAME):
            path = project.root / name
            if path.is_file():
                found[name] = path.stat().st_mtime_ns
        for folder, dirs, files in os.walk(project.root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                if f in self.watch_names or f.lower().endswith(self.watch_suffixes):
                    full = os.path.join(folder, f)
                    found[to_rel(str(project.root), full)] = os.stat(full).st_mtime_ns
        return found
