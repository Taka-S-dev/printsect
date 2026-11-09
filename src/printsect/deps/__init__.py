"""The dependency role: what each output is built from, for picking what to rebuild.

`make_scanner` picks the implementation named in `deps.type` (builtin: make). Another
build system can be added as a plugin in the entry point group `printsect.deps`; it
needs `__init__(settings)` and `scan(project, *, log=None) -> Graph`.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from ..core import plugins
from ..core.config import Project, Schema, check
from ..core.errors import PsectError
from ..core.fsutil import ensure_state_dir, write_atomic
from . import make  # noqa: F401  (registers the builtin make scanner)
from .graph import Graph
from .markdown import render_markdown

# The sections of printsect.yaml this tool reads. Its own keys are checked by the
# reader picked with deps.type, which may be a plugin with keys of its own.
SECTIONS: Schema = {"deps": {"type": str},
                    "public": {"mask": list, "hide_sources": bool}}

DEPS_FILE = "deps.json"
DEPS_MD = "DEPS.md"

HEADER_EXTENSIONS = (".h", ".hh", ".hpp", ".hxx", ".h++", ".inc")
HEADERS_OFF_NOTE = (
    "headers are not followed (deps.headers: false), so printsect cannot tell which "
    "sources a change to this header rebuilds; set deps.headers: true and run psect deps"
)


def runs_unasked(settings: dict[str, Any] | None) -> bool:
    """Whether `psect build`, already about to run the build, may read it by itself.

    Only the stock `make -n -B -w` qualifies. A command a project's config sets could be
    anything, so it runs only when asked for (`psect deps`).
    """
    from .make import DEFAULT_COMMAND

    s = settings or {}
    return s.get("type", "make") == "make" and s.get("command", DEFAULT_COMMAND) in (
        DEFAULT_COMMAND, None)


def is_header(path: str) -> bool:
    return path.lower().endswith(HEADER_EXTENSIONS)


class Scanner(Protocol):
    def scan(self, project: Project, *, log: str | None = None) -> Graph: ...


def make_scanner(settings: dict[str, Any] | None) -> Scanner:
    settings = dict(settings or {})
    kind = str(settings.pop("type", "make"))
    factory = plugins.load("deps", kind)
    schema = getattr(factory, "SETTINGS", None)  # a reader that declares its settings
    if schema is not None:
        check(settings, schema, "deps")
    scanner: Scanner = factory(settings)
    return scanner


def save(project: Project, graph: Graph) -> None:
    ensure_state_dir(project.state_dir)
    # Compact: a large project has about a million header entries.
    text = json.dumps(graph.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
    write_atomic(project.state_dir / DEPS_FILE, text)
    write_atomic(project.state_dir / DEPS_MD, render_markdown(graph))


def load(project: Project) -> Graph:
    path = project.state_file(DEPS_FILE)
    if not path.is_file():
        raise PsectError("no dependency data yet: run `psect deps` first")
    try:
        return Graph.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise PsectError(f"cannot read {path}: {exc}; run `psect deps` again") from exc
