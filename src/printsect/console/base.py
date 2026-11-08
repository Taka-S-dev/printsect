"""The console role: reading what the target prints, and sending it commands.

Every console numbers the lines it has seen, 1, 2, 3, ... A caller keeps the number
of the last line it read and asks for what came after (`read(since)`), so it never
reads a line twice and can tell when lines were lost (`missed`).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from ..core.errors import ConfigError, PsectError


@dataclass(frozen=True)
class Line:
    no: int
    text: str


class Console(Protocol):
    missed: int  # lines that went by unseen (the source kept too few)

    def mark(self) -> int:
        """The number of the last line printed so far; read(mark()) gives what follows."""
        ...

    def read(self, since: int) -> list[Line]:
        """The lines after line `since`, oldest first."""
        ...

    def send(self, text: str, timeout: float | None = None) -> str:
        """Send a command to the target; what it answered."""
        ...

    def close(self) -> None: ...


def wait_for(console: Console, pattern: str, timeout: float, since: int | None = None,
             poll: float = 0.2) -> Line | None:
    """The first line after `since` (default: now) matching `pattern`, or None in time."""
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        raise ConfigError(f"bad pattern {pattern!r}: {exc}") from exc
    cursor = console.mark() if since is None else since
    deadline = time.monotonic() + timeout
    while True:
        for line in console.read(cursor):
            cursor = line.no
            if rx.search(line.text):
                return line
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


# -- for consoles that answer in JSON ---------------------------------------------------


@dataclass
class JsonKeys:
    """Where a JSON answer keeps its lines: {"lines": [{"no": 7, "text": "..."}]}."""

    lines: str = "lines"
    no: str = "no"
    text: str = "text"
    output: str = "output"

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> JsonKeys:
        d = cls()
        return cls(
            lines=str(settings.get("lines_key", d.lines)),
            no=str(settings.get("no_key", d.no)),
            text=str(settings.get("text_key", d.text)),
            output=str(settings.get("output_key", d.output)),
        )

    def lines_of(self, data: Any, where: str) -> list[Line]:
        items = data.get(self.lines) if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise PsectError(f"{where}: the answer has no list under {self.lines!r}")
        try:
            # Programs keep a line's own end ("\r" from the target) in its text; drop it.
            return [Line(int(i[self.no]), str(i[self.text]).rstrip("\r\n")) for i in items]
        except (KeyError, TypeError, ValueError) as exc:
            raise PsectError(f"{where}: a line lacks {self.no!r} or {self.text!r}: {exc}") from exc


class Numbered:
    """Turns "the last N lines, numbered" answers into read(since), counting lost lines."""

    def __init__(self) -> None:
        self.missed = 0

    def after(self, lines: list[Line], since: int) -> list[Line]:
        new = sorted((ln for ln in lines if ln.no > since), key=lambda ln: ln.no)
        if since and new and new[0].no > since + 1:
            self.missed += new[0].no - since - 1  # scrolled away before it was read
        return new


def fill(value: Any, **values: str) -> Any:
    """{name} filled in every string inside `value` (strings, lists, mappings)."""
    if isinstance(value, str):
        for k, v in values.items():
            value = value.replace("{" + k + "}", v)
        return value
    if isinstance(value, list):
        return [fill(v, **values) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, **values) for k, v in value.items()}
    return value
