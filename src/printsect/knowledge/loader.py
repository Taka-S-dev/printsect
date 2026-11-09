"""Finding, checking and combining knowledge files. Reading one never runs anything.

Only the folders the settings name are read. A file that is a link, too large, not text,
of an unknown kind or with a field this does not know is refused and said so, not skipped
in silence: it may be something else than it looks.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import Project, user_folder
from ..core.errors import ConfigError
from ..core.fsutil import is_link, plain_or_absent

USER_PREFIX = "user-knowledge"
DEFAULT_PATHS = [".printsect/knowledge", USER_PREFIX + "/printsect"]
MAX_FILE_BYTES = 256 << 10
MAX_GIT_INDEX = 256 << 20
SUFFIXES = (".md", ".yaml", ".yml")
FIELDS = {"version", "scope", "classes", "layers", "mask", "notes"}
SCOPE_FIELDS = {"project", "build", "platform", "applies_to"}
APPLIES = ("analysis", "html", "tools")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)


class _Refused(Exception):
    """Why a file is not read."""


@dataclass
class Item:
    file: str
    version: Any
    sha256: str
    modified: str
    scope: dict[str, Any]
    data: dict[str, Any]
    body: str = ""


@dataclass
class Knowledge:
    loaded: list[Item] = field(default_factory=list)
    refused: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    classes: dict[str, list[str]] = field(default_factory=dict)
    layers: list[str] = field(default_factory=list)
    mask: list[str] = field(default_factory=list)


def _strings(value: Any, where: str) -> list[str]:
    if not (isinstance(value, list) and all(isinstance(v, str) and v for v in value)):
        raise _Refused(f"{where}: expected a list of text")
    return value


def _checked(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise _Refused("expected a mapping of fields")
    for key in data:
        if key not in FIELDS:
            raise _Refused(f"unknown field {key!r}")
    scope = data.get("scope", {})
    if not isinstance(scope, dict):
        raise _Refused("scope: expected a mapping")
    for key in scope:
        if key not in SCOPE_FIELDS:
            raise _Refused(f"scope: unknown field {key!r}")
    for applied in _strings(scope.get("applies_to", list(APPLIES)), "scope.applies_to"):
        if applied not in APPLIES:
            raise _Refused(f"scope.applies_to: one of {', '.join(APPLIES)}")
    if "classes" in data:
        if not isinstance(data["classes"], dict):
            raise _Refused("classes: expected a mapping of name to patterns")
        for name, patterns in data["classes"].items():
            _strings(patterns, f"classes.{name}")
    for key in ("layers", "mask"):
        if key in data:
            _strings(data[key], key)
    if "notes" in data and not isinstance(data["notes"], str):
        raise _Refused("notes: expected text")
    return data


def _parse(name: str, raw: bytes) -> tuple[dict[str, Any], str]:
    import yaml

    if len(raw) > MAX_FILE_BYTES:
        raise _Refused(f"over {MAX_FILE_BYTES >> 10} KB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _Refused("not UTF-8 text") from exc
    if _CONTROL.search(text):  # it would reach terminals and agents
        raise _Refused("holds control characters")
    body = ""
    try:
        if name.endswith(".md"):
            front = _FRONT.match(text)
            data = yaml.safe_load(front.group(1)) if front else {}
            body = text[front.end():] if front else text
        else:
            data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _Refused("not valid YAML") from exc
    return _checked(data if data is not None else {}), body


def _folders(project: Project) -> list[tuple[str, Path]]:
    """Each folder named, with the label its files go by (never an absolute path)."""
    settings = project.settings.get("knowledge") or {}
    found: list[tuple[str, Path]] = []
    for entry in settings.get("paths") or DEFAULT_PATHS:
        text = str(entry).replace("\\", "/")
        if text == USER_PREFIX or text.startswith(USER_PREFIX + "/"):
            # The settings can come with a project: they may pick a folder under the
            # user's knowledge, and nothing above it.
            parts = text.split("/")
            if parts[:2] != [USER_PREFIX, "printsect"] or ".." in parts or ":" in text:
                raise ConfigError(f"knowledge.paths: {entry!r} is not {USER_PREFIX}/printsect "
                                  "or a folder under it")
            found.append(("user:", user_folder().joinpath("knowledge", *parts[2:])))
            continue
        clean = posixpath.normpath(text)
        if ":" in text or text.startswith("/") or clean == ".." or clean.startswith("../"):
            raise ConfigError(f"knowledge.paths: {entry!r} is not a folder inside the project; "
                              f"put it there, or under {USER_PREFIX}/printsect")
        found.append((clean + "/", project.root / clean))
    return found


def _files(folder: Path, refused: list[dict[str, str]], label: str) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    stack = [(folder, "")]
    while stack:
        current, prefix = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            rel = prefix + entry.name
            info = entry.stat(follow_symlinks=False)
            if is_link(info):
                refused.append({"file": label + rel, "why": "a link, which is not followed"})
            elif stat.S_ISDIR(info.st_mode):
                stack.append((Path(entry.path), rel + "/"))
            elif stat.S_ISREG(info.st_mode):
                if entry.name.lower().endswith(SUFFIXES):
                    found.append((label + rel, Path(entry.path)))
                else:
                    refused.append({"file": label + rel, "why": "not a kind of file read here "
                                    f"({', '.join(SUFFIXES)})"})
    return sorted(found)


def _names_git_tracks(root: Path) -> bytes | None:
    """The index file of a git checkout, where each tracked path ends in a NUL byte. It is
    read, because running git in a project can run what the project's git settings name.
    None when there is no checkout to ask, or its index packs the paths (version 4)."""
    folder, index = root / ".git", root / ".git" / "index"
    if not plain_or_absent(folder, index) or not index.is_file():
        return None
    try:
        with open(index, "rb") as handle:
            data = handle.read(MAX_GIT_INDEX + 1)
    except OSError:
        return None
    return data if len(data) <= MAX_GIT_INDEX and data[:8] != b"DIRC\0\0\0\4" else None


def _privacy_warning(name: str, project: Project, tracked: bytes | None) -> str | None:
    """What to say about a file of private names that version control could pick up."""
    if name.startswith("user:"):
        return None
    if tracked is not None and name.encode() + b"\0" in tracked:
        return f"{name} is tracked by git, so whatever it names goes out with the repository"
    if name.startswith(".printsect/"):
        ignore = project.state_dir / ".gitignore"
        checkout = project.root / ".git"
        unsafe = (plain_or_absent(checkout) and checkout.is_dir()
                  and plain_or_absent(project.state_dir, ignore) and not ignore.is_file())
        return f"{name}: nothing keeps .printsect out of git yet" if unsafe else None
    return f"{name} is in the project: keep it out of version control if it holds private names"


def _out_of_scope(item: Item, project: Project) -> str | None:
    settings = project.settings.get("knowledge") or {}
    own = {"project": settings.get("profile") or project.root.name,
           "build": settings.get("build"), "platform": settings.get("platform")}
    for key, here in own.items():
        wanted = item.scope.get(key)
        if wanted is not None and str(wanted) != str(here):
            return f"for {key} {wanted!r}, and this is {here!r}"
    return None


def _combine(result: Knowledge) -> None:
    """Files earlier in the order win, and what lost is reported."""
    owner: dict[str, str] = {}

    def lost(key: str, item: Item) -> None:
        result.conflicts.append({"key": key, "used": owner[key], "ignored": item.file})

    for item in result.loaded:
        applies = item.scope.get("applies_to", list(APPLIES))
        if "analysis" in applies:
            for name, patterns in item.data.get("classes", {}).items():
                key = f"classes.{name}"
                if name in result.classes:
                    if result.classes[name] != patterns:
                        lost(key, item)
                else:
                    result.classes[name] = patterns
                    owner[key] = item.file
            if item.data.get("layers"):
                if not result.layers:
                    result.layers = item.data["layers"]
                    owner["layers"] = item.file
                elif result.layers != item.data["layers"]:
                    lost("layers", item)
        if "html" in applies:
            result.mask += [m for m in item.data.get("mask", []) if m not in result.mask]


def load_knowledge(project: Project) -> Knowledge:
    result = Knowledge()
    tracked = _names_git_tracks(project.root)
    for label, folder in _folders(project):
        if not plain_or_absent(folder):
            result.refused.append({"file": label, "why": "a link, which is not followed"})
            continue
        if not folder.is_dir():
            continue
        for name, path in _files(folder, result.refused, label):
            try:
                raw = path.read_bytes()
                data, body = _parse(name, raw)
            except _Refused as exc:
                result.refused.append({"file": name, "why": str(exc)})
                continue
            except OSError as exc:
                result.refused.append({"file": name, "why": exc.strerror or "cannot be read"})
                continue
            when = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            item = Item(file=name, version=data.get("version"),
                        sha256=hashlib.sha256(raw).hexdigest(),
                        modified=when.isoformat(timespec="seconds"),
                        scope=data.get("scope", {}), data=data,
                        body=(data.get("notes") or "") + body)
            why = _out_of_scope(item, project)
            if why:
                result.skipped.append({"file": name, "why": why})
                continue
            result.loaded.append(item)
            warning = _privacy_warning(name, project, tracked)
            if warning:
                result.warnings.append(warning)
    _combine(result)
    return result
