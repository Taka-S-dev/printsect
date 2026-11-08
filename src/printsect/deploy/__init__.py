"""The deployer role. `make_deployer` picks the implementation named in the config."""

from __future__ import annotations

from typing import Any

from ..core import plugins
from ..core.config import Schema, check
from . import deployers, ftp  # noqa: F401  (registers the builtin deployers)
from .base import FAILED, OK, WAITING_USER, DeployContext, Deployer, DeployResult

# The sections of printsect.yaml this tool owns. deploy's own keys are checked by the
# deployer picked with deploy.type, which may be a plugin with keys of its own. It also
# reads build.artifact, but build: belongs to the build (psect run, psect check), so
# this tool does not judge its other keys.
SECTIONS: Schema = {"deploy": {"type": str}}

__all__ = [
    "FAILED", "OK", "WAITING_USER", "DeployContext", "DeployResult", "Deployer",
    "make_deployer",
]


def make_deployer(settings: dict[str, Any] | None) -> Deployer:
    settings = dict(settings or {})
    kind = str(settings.pop("type", "none"))
    factory = plugins.load("deployer", kind)
    schema = getattr(factory, "SETTINGS", None)  # a deployer that declares its settings
    if schema is not None:
        check(settings, schema, "deploy")
    deployer: Deployer = factory(settings)
    return deployer
