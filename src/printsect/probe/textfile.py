"""Reading and writing a source file without changing anything but the lines we touch.

The encoding, each line's own line ending, a missing final newline and a BOM all come
back exactly as they were. A file whose bytes would not survive a decode/encode round
trip is refused rather than rewritten.
"""

from __future__ import annotations

import codecs
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import PsectError


def split_lines(text: str) -> list[str]:
    """Lines with their own ending kept. Only \\n ends a line (a form feed does not)."""
    if not text:
        return []
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def body(line: str) -> str:
    """A line without its ending."""
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n"):
        return line[:-1]
    return line


def ending(line: str) -> str:
    return line[len(body(line)) :]


@dataclass
class TextFile:
    path: Path
    encoding: str
    bom: bool
    lines: list[str]
    mtime_ns: int
    eol: str = field(default="\n")

    @classmethod
    def read(cls, path: Path, encodings: list[str]) -> TextFile:
        try:
            stat = path.stat()
            data = path.read_bytes()
        except OSError as exc:
            raise PsectError(f"cannot read {path}: {exc.strerror}") from exc
        bom = data.startswith(codecs.BOM_UTF8)
        candidates = ["utf-8"] if bom else encodings
        raw = data[len(codecs.BOM_UTF8) :] if bom else data
        for encoding in candidates:
            try:
                text = raw.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
            if text.encode(encoding) != raw:
                raise PsectError(
                    f"{path}: decoding as {encoding} does not give back the same bytes; "
                    "not editing it"
                )
            lines = split_lines(text)
            crlf = sum(1 for line in lines if line.endswith("\r\n"))
            lf = sum(1 for line in lines if line.endswith("\n")) - crlf
            eol = "\r\n" if crlf > lf else "\n"
            return cls(path, encoding, bom, lines, stat.st_mtime_ns, eol)
        raise PsectError(f"{path}: not readable as {', '.join(candidates)}")

    def insert(self, index: int, new: list[str]) -> None:
        """Put `new` (bodies, no endings) before line `index` (0-based)."""
        if not new:
            return
        if index > 0 and index == len(self.lines) and not ending(self.lines[-1]):
            # Appending after a last line with no newline: it gets one, and the last
            # inserted line goes without, so the file still ends the way it did.
            self.lines[-1] += self.eol
            added = [line + self.eol for line in new[:-1]] + [new[-1]]
        else:
            added = [line + self.eol for line in new]
        self.lines[index:index] = added

    def delete(self, indexes: set[int]) -> None:
        """Take out lines by 0-based index. A file that ended without a newline still does."""
        if not indexes:
            return
        had_final_newline = bool(self.lines) and bool(ending(self.lines[-1]))
        self.lines = [line for i, line in enumerate(self.lines) if i not in indexes]
        if self.lines and not had_final_newline:
            self.lines[-1] = body(self.lines[-1])

    def write(self) -> None:
        try:
            now = self.path.stat().st_mtime_ns
        except OSError as exc:
            raise PsectError(f"cannot read {self.path}: {exc.strerror}") from exc
        if now != self.mtime_ns:
            raise PsectError(f"{self.path} changed while it was being edited; run it again")
        data = "".join(self.lines).encode(self.encoding)
        if self.bom:
            data = codecs.BOM_UTF8 + data
        mode = self.path.stat().st_mode
        fd, tmp = tempfile.mkstemp(prefix=".psect-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.chmod(tmp, mode)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self.mtime_ns = self.path.stat().st_mtime_ns
