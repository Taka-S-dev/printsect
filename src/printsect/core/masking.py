"""Taking names out of what is handed on, and checking what is about to be written.

Masking works on what it is told and what it can recognise, so it is followed by a check
of the text. A value the check finds stops the page; it is not trusted to have been caught.
"""

from __future__ import annotations

import re
from collections import Counter

from .errors import PsectError

_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/])")
_DRIVE_OR_HOME = re.compile(r"[A-Za-z]:[\\/][^\"\s,]*|(?:/home|/Users|/mnt|/opt|/srv)/[^\"\s,]+")
_SUFFIX = re.compile(r"\.[A-Za-z0-9+]{1,8}$")
_SECRET = re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key)\b\s*[=:]\s*[^\s\",]+")


class Masker:
    """Rewrites names the same way every time, and counts what it did."""

    def __init__(self, names: list[str], hide_sources: bool) -> None:
        for name in names:
            if not (isinstance(name, str) and name.strip()):
                raise PsectError(f"public.mask: expected non-empty text, got {name!r}")
        self.names = sorted(set(names), key=len, reverse=True)
        self.hide_sources = hide_sources
        self.counts: Counter[str] = Counter()
        self._labels = {n: f"name{i}" for i, n in enumerate(self.names, 1)}
        self._rule = (re.compile("|".join(re.escape(n) for n in self.names), re.I)
                      if self.names else None)
        self._by_lower = {n.lower(): self._labels[n] for n in self.names}
        self._files: dict[str, str] = {}
        self._modules: dict[str, str] = {}
        self._targets: dict[str, str] = {}
        self._symbols: dict[str, str] = {}

    def _one(self, match: re.Match[str]) -> str:
        self.counts["names"] += 1
        return self._by_lower[match.group().lower()]

    def text(self, value: str) -> str:
        return self._rule.sub(self._one, value) if self._rule else value

    def path(self, value: str) -> str:
        if _ABSOLUTE.match(value):
            self.counts["absolute_paths"] += 1
            value = "(outside)/" + re.split(r"[\\/]", value)[-1]
        return self.text(value)

    def file(self, value: str) -> str:
        """A source, header or object: its name too when source names are hidden."""
        if not self.hide_sources:
            return self.path(value)
        if value not in self._files:
            self.counts["files_hidden"] += 1
            # From the last part of the path only, and only if it looks like a suffix:
            # "acme.board/rx" must not give away "acme.board/rx".
            suffix = _SUFFIX.search(re.split(r"[\\/]", value)[-1])
            self._files[value] = f"file-{len(self._files) + 1}" + (suffix.group() if suffix else "")
        return self._files[value]

    def module(self, value: str) -> str:
        """A module of the map: folders and archives name the code as much as files do."""
        if not self.hide_sources:
            return self.path(value)
        return self._modules.setdefault(value, f"module-{len(self._modules) + 1}")

    def target(self, value: str) -> str:
        """Where something is deployed: hosts and paths on a target, never handed on."""
        self.counts["targets"] += value not in self._targets
        return self._targets.setdefault(value, f"target-{len(self._targets) + 1}")

    def symbol(self, name: str) -> str:
        """One function or variable, kept apart from the others when source names are hidden."""
        if not self.hide_sources:
            return self.text(name)
        if name not in self._symbols:
            self.counts["symbols_hidden"] += 1
            self._symbols[name] = f"symbol-{len(self._symbols) + 1}"
        return self._symbols[name]

    def symbols(self, names: list[str]) -> list[str]:
        return [] if self.hide_sources else [self.text(n) for n in names]

    def report(self) -> dict[str, int]:
        return {k: self.counts[k] for k in ("names", "absolute_paths", "files_hidden", "targets")}


def leaks(text: str, names: list[str], root: str, own: str | None = None) -> list[str]:
    """What is still in `text` that must not go out: kinds and counts, never the values.
    Names to mask are looked for in `own` when given: the parts from the project."""
    found = []
    drives = len(_DRIVE_OR_HOME.findall(text))
    if drives:
        found.append(f"absolute paths: {drives}")
    secrets = len(_SECRET.findall(text))
    if secrets:
        found.append(f"something like a password or key: {secrets}")
    low = (text if own is None else own).lower()
    named = sum(low.count(n.lower()) for n in names)
    if named:
        found.append(f"names to mask: {named}")
    low = text.lower()
    if root and root.lower().replace("\\", "/") in low.replace("\\/", "/"):
        found.append("this project's folder")
    return found
