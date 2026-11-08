"""The console role: what the target prints, read by line number, and commands to it.

`make_console` picks the implementation named in `console.type`. Another way to reach
a console can be added as a plugin in the entry point group `printsect.console`; it
needs `__init__(settings)`, `missed`, `mark()`, `read(since)`, `send(text, timeout)`
and `close()` (see base.Console), and may declare its keys as `SETTINGS`.
"""

from __future__ import annotations

from typing import Any

from ..core import plugins
from ..core.config import Schema, check
from ..core.errors import ConfigError
from . import consoles  # noqa: F401  (registers the builtin consoles)
from .base import Console, Line, wait_for

__all__ = ["SECTIONS", "Console", "Line", "make_console", "wait_for"]

# The sections of printsect.yaml this tool reads. console's own keys are checked by
# the console picked with console.type, which may be a plugin with keys of its own.
SECTIONS: Schema = {"console": {"type": str}}


def make_console(settings: dict[str, Any] | None) -> Console:
    settings = dict(settings or {})
    kind = settings.pop("type", None)
    if not kind:
        raise ConfigError("no console is set up: add console: {type: serial|tcp|command|file}")
    factory = plugins.load("console", str(kind))
    schema = getattr(factory, "SETTINGS", None)
    if schema is not None:
        check(settings, schema, "console")
    console: Console = factory(settings)
    return console
