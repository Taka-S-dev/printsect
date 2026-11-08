"""Small file helpers shared by the parts that write into the state folder."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

# The kinds of Windows link; other reparse points (cloud files) are ordinary files.
_LINK_TAGS = (0xA000000C, 0xA0000003)


def is_link(info: os.stat_result) -> bool:
    """Whether an lstat result is a symbolic link or a junction."""
    return stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) in _LINK_TAGS


def plain_or_absent(*paths: Path) -> bool:
    """Whether each path, parents first, is missing or a real file or folder.

    A project can come with links in its state folder, and opening one that leads to a
    network share hands that server this PC's login: a tool that runs before anyone has
    approved the project looks first.
    """
    for path in paths:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError:
            return False
        if is_link(info) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            return False
    return True


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
