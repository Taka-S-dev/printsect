"""The code map role: which module uses which, read from the source without a build.

`make_indexer` picks the implementation named in `map.type` (builtin: scan). Another
index can be added as a plugin in the entry point group `printsect.indexer`; it needs
`__init__(settings)` and `index(project, log) -> Index`.
"""

from __future__ import annotations

from typing import Any

from ..core import plugins
from ..core.config import Schema, check
from . import index as _index  # noqa: F401  (registers the builtin indexer)
from .index import FileEntry, Index

# The sections of printsect.yaml this tool reads. Its own keys are checked by the
# indexer picked with map.type, which may be a plugin with keys of its own.
SECTIONS: Schema = {"map": {"type": str}}

__all__ = ["FileEntry", "Index", "SECTIONS", "make_indexer"]


def make_indexer(settings: dict[str, Any] | None) -> Any:
    settings = dict(settings or {})
    kind = str(settings.pop("type", "scan"))
    factory = plugins.load("indexer", kind)
    schema = getattr(factory, "SETTINGS", None)
    if schema is not None:
        check(settings, schema, "map")
    return factory(settings)
