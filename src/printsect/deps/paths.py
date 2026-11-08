"""Turning the paths a build prints into the graph's paths."""

from __future__ import annotations

import os
import re
from collections.abc import Callable

_MSYS_DRIVE = re.compile(r"^/([A-Za-z])(?=/|$)")


def native(path: str) -> str:
    """On Windows, `/c/work` as printed by an MSYS make is `C:/work`."""
    if os.name == "nt":
        path = _MSYS_DRIVE.sub(lambda m: m.group(1).upper() + ":", path)
    return path


def join(cwd: str, path: str) -> str:
    """`path` as seen from `cwd`, made absolute and normalised (no file system access)."""
    path = native(path)
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path))


def to_rel(root: str, path: str) -> str:
    """Relative to `root` with forward slashes, or absolute when outside it."""
    try:
        rel = os.path.relpath(path, root)
    except ValueError:  # another drive
        return path.replace("\\", "/")
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return path.replace("\\", "/")
    return rel.replace("\\", "/")


def to_abs(root: str, rel: str) -> str:
    return os.path.normpath(rel if os.path.isabs(rel) else os.path.join(root, rel))


def relativizer(root: str) -> tuple[Callable[[str], str], Callable[[str], bool]]:
    """A fast `to_rel` for many paths under one root, for paths `join` already made.

    Returns (rel, inside): rel(path) is the graph's form of the path; inside(path) says
    whether it is under the root. Both compare without touching the file system.
    """
    prefix = os.path.normcase(os.path.normpath(root)).rstrip("\\/") + os.sep
    cut = len(prefix)

    def inside(path: str) -> bool:
        return os.path.normcase(path).startswith(prefix)

    def rel(path: str) -> str:
        if inside(path):
            return path[cut:].replace("\\", "/")
        return path.replace("\\", "/")

    return rel, inside
