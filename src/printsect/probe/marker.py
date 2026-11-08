"""The mark that ends every probe line, and the tag a probe prints.

    printf("[PSECT-AI 7 len] len=%d\\n", len); /* PSECT-AI:len #7 */

The mark is what finds a probe again: no line number is trusted. `-AI` marks a line an
agent put in; only those are ever taken out automatically. A line in a macro keeps its
continuation backslash after the mark.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GROUP_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class Mark:
    ai: bool
    group: str | None
    n: int | None


class Marker:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        core = (
            rf"{re.escape(prefix)}(?P<ai>-AI)?(?::(?P<group>[A-Za-z0-9_.-]+))?"
            rf"(?:\s+#(?P<n>\d+))?"
        )
        self._block = re.compile(rf"/\*\s*{core}\s*\*/(?P<cont>\s*\\)?\s*$")
        self._line = re.compile(rf"//\s*{core}\s*$")
        # A cheap test before the regexes, for scanning whole trees.
        self.needle = prefix

    def parse(self, line: str) -> Mark | None:
        if self.needle not in line:
            return None
        match = self._block.search(line) or self._line.search(line)
        if not match:
            return None
        n = match.group("n")
        return Mark(ai=bool(match.group("ai")), group=match.group("group"), n=int(n) if n else None)

    def comment(self, ai: bool, group: str | None, n: int) -> str:
        who = f"{self.prefix}-AI" if ai else self.prefix
        name = f":{group}" if group else ""
        return f"/* {who}{name} #{n} */"

    def tag(self, ai: bool, group: str | None, n: int) -> str:
        who = f"{self.prefix}-AI" if ai else self.prefix
        return f"{who} {n} {group}" if group else f"{who} {n}"

    def tag_re(self) -> re.Pattern[str]:
        """Finds `[PSECT-AI 7 group]` / `[PSECT 3]` in program output."""
        return re.compile(
            rf"\[{re.escape(self.prefix)}(?P<ai>-AI)? (?P<n>\d+)(?: (?P<group>[A-Za-z0-9_.-]+))?\]"
        )

    def mark_line(self, text: str, ai: bool, group: str | None, n: int) -> str:
        """`text` with the mark on its end; a trailing `\\` stays last."""
        stripped = text.rstrip()
        comment = self.comment(ai, group, n)
        if stripped.endswith("\\"):
            return f"{stripped[:-1].rstrip()} {comment} \\"
        return f"{stripped} {comment}"
