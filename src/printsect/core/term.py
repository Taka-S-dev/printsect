"""Terminal helpers: whether to colour, and how wide text is on screen."""

from __future__ import annotations

import os
import sys
import unicodedata
from typing import TextIO

BOLD_YELLOW = "\x1b[1;33m"
CYAN = "\x1b[36m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"


def _enable_windows_vt() -> bool:
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def use_color(stream: TextIO = sys.stdout) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if os.name == "nt":
        return _enable_windows_vt()
    return os.environ.get("TERM") != "dumb"


def width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
