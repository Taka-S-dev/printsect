"""Counting what each probe printed: how often, where in the log, and the values it showed.

A probe prints its tag, `[PSECT-AI 7 rx] len=0 state=2`. Every line with a tag is a hit
for that probe; `name=value` pairs after the tag are kept, a few distinct values each,
so "len was 0 every time" or "state went 1, 2, 3" can be read without the whole log.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from .marker import Marker

_VALUE_RE = re.compile(r"([A-Za-z_][\w.\[\]>-]*)\s*=\s*([^\s,;]+)")
KEEP_VALUES = 5  # distinct values kept per name
KEEP_SAMPLES = 3  # lines kept per probe


@dataclass
class Hit:
    n: int
    group: str | None = None
    hits: int = 0
    first: int | None = None  # line number of the first hit
    last: int | None = None
    samples: list[str] = field(default_factory=list)
    values: dict[str, list[str]] = field(default_factory=dict)
    more_values: bool = False  # some names had more distinct values than kept

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def count(lines: Iterable[tuple[int, str]], prefix: str, ai_only: bool = False) -> dict[int, Hit]:
    """Hits per probe number in numbered lines (line number, text)."""
    tag_re = Marker(prefix).tag_re()
    found: dict[int, Hit] = {}
    for no, text in lines:
        for m in tag_re.finditer(text):
            if ai_only and not m.group("ai"):
                continue
            n = int(m.group("n"))
            hit = found.setdefault(n, Hit(n, m.group("group")))
            hit.hits += 1
            hit.first = no if hit.first is None else hit.first
            hit.last = no
            if len(hit.samples) < KEEP_SAMPLES:
                hit.samples.append(text.strip())
            rest = text[m.end():]
            nxt = tag_re.search(rest)
            for name, value in _VALUE_RE.findall(rest[: nxt.start()] if nxt else rest):
                kept = hit.values.setdefault(name, [])
                if value in kept:
                    continue
                if len(kept) < KEEP_VALUES:
                    kept.append(value)
                else:
                    hit.more_values = True
    return found


def numbered(text: str) -> list[tuple[int, str]]:
    """A plain log as numbered lines, 1, 2, 3, ..."""
    return list(enumerate(text.splitlines(), start=1))
