"""The builtin indexer: every C file in the project, re-read only when it changed.

It only ever reads: no command runs, so the map needs no approval and no set-up. Links
are not followed, because a project's link could lead anywhere on this PC (or to a
network share, which is contacted just by being opened).
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import Project
from ..core.errors import ConfigError
from ..core.fsutil import ensure_state_dir, is_link, write_atomic
from ..core.plugins import register
from .scanner import first_uses, scan, use_lines

INDEX_FILE = "map.json"
INDEX_VERSION = 3
MAX_FILE_BYTES = 4 << 20
DEFAULT_EXTENSIONS = (".c", ".cc", ".cpp", ".cxx")
DEFAULT_ENCODINGS = ("utf-8", "cp932")
_NOTE_FROM = 200
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NAMES = re.compile(r"[A-Za-z0-9_ ]*")


@dataclass
class FileEntry:
    defs: list[tuple[str, str, bool, int]]
    refs: list[str]
    macro_defs: int = 0
    unbalanced: bool = False
    too_long: int = 0
    inside_if: int = 0


@dataclass
class Index:
    files: dict[str, FileEntry] = field(default_factory=dict)
    too_large: int = 0
    unreadable: int = 0
    links: int = 0
    excluded: int = 0  # files and folders map.exclude took out
    confirmed: bool = False  # names were matched by a compiler, not by shape alone


@register("indexer", "scan")
class ScanIndexer:
    SETTINGS = {"exclude": list, "extensions": list, "encodings": list, "classes": dict,
                "layers": list}

    def __init__(self, settings: dict[str, Any]) -> None:
        self.exclude = [str(p).replace("\\", "/").rstrip("/")
                        for p in settings.get("exclude") or []]
        self.extensions = tuple(str(e).lower() for e in
                                settings.get("extensions") or DEFAULT_EXTENSIONS)
        self.encodings = [str(e) for e in settings.get("encodings") or DEFAULT_ENCODINGS]
        for name in self.encodings:
            try:
                "".encode(name)
            except LookupError as exc:
                raise ConfigError(f"map.encodings: no encoding named {name!r}") from exc

    def _excluded(self, rel: str) -> bool:
        return any(fnmatch.fnmatchcase(rel, p) for p in self.exclude)

    def _files(self, root: Path, index: Index) -> Iterator[tuple[str, os.stat_result]]:
        stack = [(str(root), "")]
        while stack:
            folder, prefix = stack.pop()
            try:
                entries = list(os.scandir(folder))
            except OSError:
                index.unreadable += 1
                continue
            for entry in entries:
                rel = prefix + entry.name
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    index.unreadable += 1
                    continue
                if _CONTROL.search(entry.name):  # it would reach terminals and agents
                    index.unreadable += 1
                elif is_link(info):
                    # What it leads to is not looked at, so a folder is told by its name.
                    if "." not in entry.name or rel.lower().endswith(self.extensions):
                        index.links += 1
                elif stat.S_ISDIR(info.st_mode):
                    if self._excluded(rel):
                        index.excluded += 1
                    elif not entry.name.startswith("."):
                        stack.append((entry.path, rel + "/"))
                elif stat.S_ISREG(info.st_mode) and rel.lower().endswith(self.extensions):
                    if self._excluded(rel):
                        index.excluded += 1
                    else:
                        yield rel, info

    def _text(self, data: bytes) -> str:
        for name in self.encodings:
            try:
                return data.decode(name)
            except UnicodeDecodeError:
                continue
        return data.decode("latin-1")  # the names are ASCII; only comments would suffer

    def _fingerprint(self) -> str:
        return json.dumps([INDEX_VERSION, sorted(self.extensions), self.encodings])

    def _load(self, path: Path) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["fingerprint"] != self._fingerprint():
                return {}
            files = data["files"]
            for item in files.values():  # the folder may come with the project: trust nothing
                if not (isinstance(item["m"], int) and isinstance(item["s"], int)
                        and isinstance(item["r"], str) and _NAMES.fullmatch(item["r"])
                        and isinstance(item["d"], list)
                        and isinstance(item["x"], int) and isinstance(item["b"], int)
                        and isinstance(item["l"], int) and isinstance(item["c"], int)):
                    return {}
                for name, kind, static, line in item["d"]:
                    if not (isinstance(name, str) and _NAME.fullmatch(name) and kind in "fv"
                            and isinstance(static, int) and isinstance(line, int)):
                        return {}
            return dict(files)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {}

    def lines_of(self, project: Project, file: str, name: str) -> list[int]:
        """The lines of `file`, one this index found, that use `name`. Read when asked,
        not kept: the saved index stays small and only the files on screen are opened."""
        try:
            with open(project.root / file, "rb") as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
        except OSError:
            return []
        return use_lines(self._text(data), name)

    def first_lines(self, project: Project, file: str, names: set[str]) -> dict[str, int]:
        """The first line of `file` using each of `names`, for the page."""
        try:
            with open(project.root / file, "rb") as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
        except OSError:
            return {}
        return first_uses(self._text(data), names)

    def index(self, project: Project, log: Callable[[str], None]) -> Index:
        path = project.state_file(INDEX_FILE)
        cached = self._load(path)
        index = Index()
        fresh: dict[str, dict[str, Any]] = {}
        todo: list[tuple[str, os.stat_result]] = []
        for rel, info in self._files(project.root, index):
            item = cached.get(rel)
            if item and item["m"] == info.st_mtime_ns and item["s"] == info.st_size:
                fresh[rel] = item
            elif info.st_size > MAX_FILE_BYTES:
                index.too_large += 1
            else:
                todo.append((rel, info))
        if len(todo) >= _NOTE_FROM:
            log(f"note: reading {len(todo)} source file(s) for the map ...")
        for rel, info in todo:
            try:
                with open(project.root / rel, "rb") as handle:
                    data = handle.read(MAX_FILE_BYTES + 1)
            except OSError:
                index.unreadable += 1
                continue
            found = scan(self._text(data))
            fresh[rel] = {"m": info.st_mtime_ns, "s": info.st_size,
                          "d": [[n, k, int(st), line] for n, k, st, line in found.defs],
                          "r": " ".join(found.refs),
                          "x": found.macro_defs, "b": int(found.unbalanced),
                          "l": found.too_long, "c": found.inside_if}
        for rel, item in fresh.items():
            index.files[rel] = FileEntry(
                defs=[(str(n), str(k), bool(st), int(line)) for n, k, st, line in item["d"]],
                refs=item["r"].split(), macro_defs=int(item["x"]), unbalanced=bool(item["b"]),
                too_long=int(item["l"]), inside_if=int(item["c"]))
        if todo or len(fresh) != len(cached):
            try:
                ensure_state_dir(project.state_dir)
                write_atomic(path, json.dumps(
                    {"fingerprint": self._fingerprint(), "files": fresh},
                    ensure_ascii=False, separators=(",", ":")))
            except OSError as exc:
                log(f"note: the map index could not be saved: {exc.strerror or exc}")
        return index
