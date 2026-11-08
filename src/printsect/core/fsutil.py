"""Small file helpers shared by the parts that write into the state folder."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_atomic(path: Path, text: str) -> None:
    """Write `text` as UTF-8 with LF, so a reader never sees half a file."""
    fd, tmp = tempfile.mkstemp(prefix=".psect-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def ensure_state_dir(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    ignore = state_dir / ".gitignore"
    if not ignore.exists():
        # Keeps the state out of git without touching the project's own ignore file.
        ignore.write_text("*\n", encoding="utf-8")
