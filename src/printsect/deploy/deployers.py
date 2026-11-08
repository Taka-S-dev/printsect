"""Deployers that ship with printsect. Anything target-specific belongs in a plugin."""

from __future__ import annotations

import subprocess
from typing import Any

from ..core.plugins import register
from .base import FAILED, OK, WAITING_USER, DeployContext, DeployResult


def _confirm(ctx: DeployContext, settings: dict[str, Any], result: DeployResult) -> DeployResult:
    """When `confirm` is set (default: the run banner), wait for it on the console."""
    if result.status != OK:
        return result
    pattern = settings.get("confirm", ctx.run_banner)
    if not pattern or ctx.wait_for is None:
        return result
    timeout = float(settings.get("confirm_timeout", 60))
    line = ctx.wait_for(ctx.fill(pattern), timeout)
    if line is None:
        return DeployResult(
            FAILED,
            f"the new build did not show '{ctx.fill(pattern)}' within {timeout:g}s; "
            "an old build may still be running",
        )
    result.details["confirmed_by"] = line
    return result


@register("deployer", "none")
class NoDeploy:
    SETTINGS: dict[str, Any] = {}

    """Nothing to put anywhere: the build is run where it was made (a PC program)."""

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings

    def deploy(self, ctx: DeployContext) -> DeployResult:
        return DeployResult(OK, "nothing to deploy")


@register("deployer", "manual")
class ManualDeploy:
    SETTINGS = {"message": str, "until": str, "timeout": (int, float)}

    """A person puts the build on the target (a vendor tool, a card, ...).

    deploy:
      type: manual
      message: 'Transfer {artifact_name} with the engineering tool'
      until: '\\[PSECT-RUN {run}\\]'   # go on by itself when this shows; default: run banner
      timeout: 600
    """

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings

    def deploy(self, ctx: DeployContext) -> DeployResult:
        message = ctx.fill(self.settings.get("message", "Put {artifact_name} on the target"))
        until = ctx.fill(self.settings.get("until", ctx.run_banner))
        timeout = float(self.settings.get("timeout", 600))
        ctx.log(f"waiting for you: {message}")
        if ctx.wait_for is not None and until:
            ctx.log(f"(going on by itself when '{until}' shows; up to {timeout:g}s)")
            line = ctx.wait_for(until, timeout)
            if line is not None:
                return DeployResult(OK, "the new build is running", {"confirmed_by": line})
            return DeployResult(WAITING_USER, f"not seen yet: {until}. {message}")
        if ctx.ask is not None:
            answer = ctx.ask(f"{message} - press Enter when done (or type 'skip')")
            if answer is not None and answer.strip().lower() != "skip":
                return DeployResult(OK, "done by hand")
        return DeployResult(WAITING_USER, message)


@register("deployer", "command")
class CommandDeploy:
    SETTINGS = {"commands": list, "command": str, "timeout": (int, float),
                "confirm": str, "confirm_timeout": (int, float)}

    """One or more commands on this PC (a transfer tool, a flashing tool, a script).

    deploy:
      type: command
      commands:
        - 'my-transfer {artifact} 192.168.0.10:/tmp/'
      timeout: 120
      confirm: '\\[PSECT-RUN {run}\\]'   # optional; default: the run banner
    """

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        commands = settings.get("commands") or ([settings["command"]] if "command" in settings
                                                else [])
        if not commands:
            from ..core.errors import ConfigError

            raise ConfigError("deploy type 'command' needs 'commands'")
        self.commands = [str(c) for c in commands]

    def deploy(self, ctx: DeployContext) -> DeployResult:
        timeout = float(self.settings.get("timeout", 120))
        outputs = []
        for command in self.commands:
            line = ctx.fill(command)
            ctx.log(f"$ {line}")
            try:
                done = subprocess.run(
                    line, shell=True, cwd=ctx.root, capture_output=True, text=True,
                    errors="replace", timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return DeployResult(FAILED, f"timed out after {timeout:g}s: {line}")
            outputs.append({"command": line, "code": done.returncode,
                            "output": (done.stdout + done.stderr)[-2000:]})
            if done.returncode != 0:
                return DeployResult(FAILED, f"exit {done.returncode}: {line}",
                                    {"commands": outputs})
        return _confirm(ctx, self.settings, DeployResult(OK, "deployed", {"commands": outputs}))
