"""Searching paths: part of a path (any case), a glob such as `*.h`, or a regex."""

from __future__ import annotations

import fnmatch
import os
import re


class Matcher:
    """A search: part of a path (any case), a glob such as `*.h`, or a regular expression."""

    def __init__(self, pattern: str, regex: bool = False) -> None:
        self.pattern = pattern
        self.glob = not regex and any(c in pattern for c in "*?[")
        if regex:
            try:
                self._rx = re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                from ..core.errors import ConfigError

                raise ConfigError(f"bad regular expression {pattern!r}: {exc}") from exc
        elif self.glob:
            self._rx = re.compile(fnmatch.translate(pattern), re.IGNORECASE)
        else:
            self._rx = re.compile(re.escape(pattern), re.IGNORECASE)

    def __call__(self, text: str | None) -> bool:
        if not text:
            return False
        if self.glob:
            return bool(self._rx.match(text) or self._rx.match(os.path.basename(text)))
        return bool(self._rx.search(text))

    def spans(self, label: str) -> list[tuple[int, int]]:
        if not self.glob:
            return [m.span() for m in self._rx.finditer(label) if m.end() > m.start()]
        spans, pos = [], 0
        for word in label.split(" "):
            if self(word):
                spans.append((pos, pos + len(word)))
            pos += len(word) + 1
        return spans
