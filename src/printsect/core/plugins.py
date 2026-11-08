"""Finding the implementation of a role by name.

A role (deployer, console, code_index, ...) has implementations that ship with
printsect (builtin) and ones that come from other installed packages through the
entry point group `printsect.<role>`. A package installed next to printsect can add an
implementation, or replace a builtin one by using the same name, without printsect
knowing anything about it:

    # pyproject.toml of a separate package
    [project.entry-points."printsect.deployer"]
    mytool = "my_package.deploy:MyDeployer"

The configuration then picks it: `deploy: {type: mytool, ...}`.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any

from .errors import ConfigError

_builtin: dict[str, dict[str, Callable[..., Any]]] = {}


def register(role: str, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator for builtin implementations."""

    def wrap(factory: Callable[..., Any]) -> Callable[..., Any]:
        _builtin.setdefault(role, {})[name] = factory
        return factory

    return wrap


def _installed(role: str) -> dict[str, Any]:
    return {ep.name: ep for ep in entry_points(group=f"printsect.{role}")}


def available(role: str) -> dict[str, str]:
    """Name -> where it comes from, for `psect plugins`."""
    out = {name: "builtin" for name in _builtin.get(role, {})}
    for name, ep in _installed(role).items():
        out[name] = f"plugin ({ep.value})"
    return out


def load(role: str, name: str) -> Callable[..., Any]:
    """The factory for `name`. An installed plugin wins over a builtin of the same name."""
    installed = _installed(role)
    if name in installed:
        try:
            loaded: Callable[..., Any] = installed[name].load()
            return loaded
        except Exception as exc:  # a broken plugin should say which one
            raise ConfigError(f"{role} '{name}' could not be loaded: {exc}") from exc
    factory: Callable[..., Any] | None = _builtin.get(role, {}).get(name)
    if factory is None:
        names = ", ".join(sorted(available(role))) or "none"
        raise ConfigError(f"no {role} named '{name}' (available: {names})")
    return factory
