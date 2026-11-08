"""The deployer role: getting a new build onto the target and running.

printsect only knows this interface. How the build gets there - a person pressing a
button in a vendor tool, a file transfer, a flashing tool, a reboot - is entirely up
to the implementation picked in the configuration:

    deploy:
      type: manual          # builtin: none | manual | command | ftp, or a plugin's name
      ...                   # everything else is handed to the implementation

An implementation is a class taking `(settings: dict)` with a `deploy(ctx)` method.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# Outcomes of a deploy.
OK = "ok"  # the new build is in and running
FAILED = "failed"  # it did not get there; `message` says why
WAITING_USER = "waiting_user"  # a person has to do something; run again to go on


@dataclass
class DeployContext:
    root: Path
    run: int  # this run's number; the build is expected to print [<prefix>-RUN <run>]
    artifact: Path | None  # what the build made, when the configuration names it
    prefix: str
    # Ask a person something and wait for the answer (None = nobody to ask, e.g. MCP).
    ask: Callable[[str], str | None] | None = None
    # Wait up to `timeout` seconds for `pattern` in the target's output; None if there
    # is no console to watch. Returns the matching line, or None on timeout.
    wait_for: Callable[[str, float], str | None] | None = None
    log: Callable[[str], None] = print
    device: str = ""  # the device in use, when devices are set up
    # Send a command to the target's console and return its answer; None if there is
    # no console to send to (then a deployer hands the command to a person).
    send: Callable[[str, float], str] | None = None

    def fill(self, text: str) -> str:
        """Fill the placeholders a configuration may use in commands and messages."""
        values = {
            "run": str(self.run),
            "artifact": str(self.artifact or ""),
            "artifact_name": self.artifact.name if self.artifact else "",
            "root": str(self.root),
            "prefix": self.prefix,
            "device": self.device,
        }
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        return text

    @property
    def run_banner(self) -> str:
        """The pattern that shows this run's build is the one running."""
        return rf"\[{self.prefix}-RUN {self.run}\]"


@dataclass
class DeployResult:
    status: str
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class Deployer(Protocol):
    def __init__(self, settings: dict[str, Any]) -> None: ...

    def deploy(self, ctx: DeployContext) -> DeployResult: ...
