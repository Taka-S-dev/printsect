"""Time stamps as printsect writes them."""

from __future__ import annotations

from datetime import datetime


def now() -> str:
    """Local time with its offset, to the second: 2026-11-08T13:45:02+09:00."""
    return datetime.now().astimezone().isoformat(timespec="seconds")
