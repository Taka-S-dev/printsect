"""Putting probes in, checking them against the source, taking them out."""

from __future__ import annotations

import os
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import STATE_DIR, Project
from ..core.errors import PsectError
from ..core.timeutil import now
from .cscan import enclosing_function, ends_statement
from .ledger import ACTIVE, MISSING, REMOVED, Ledger, Probe
from .marker import GROUP_RE, Mark, Marker
from .textfile import TextFile, body

# The run banner is kept like a probe, in its own group, and does not count as one.
BANNER_GROUP = "run"
BANNER_TEMPLATE = r'printf("[{prefix}-RUN {run}]\n");'

SKIP_DIRS = {".git", ".svn", ".hg", STATE_DIR, "node_modules", "__pycache__"}


@dataclass
class Found:
    file: str
    line: int  # 1-based
    text: str
    mark: Mark


def _indent(text: str) -> str:
    return text[: len(text) - len(text.lstrip(" \t"))]


def _unit(lines: list[str]) -> str:
    if any(line.startswith("\t") for line in lines):
        return "\t"
    widths = [len(_indent(body(line))) for line in lines if body(line).strip()]
    widths = [w for w in widths if w > 0]
    return " " * min(min(widths, default=4), 8)


def indent_for(lines: list[str], index: int) -> str:
    """The indent for a line put in after line `index` (0-based; -1 for the top)."""
    if index < 0:
        return ""
    here = body(lines[index])
    after = next((body(line) for line in lines[index + 1 :] if body(line).strip()), None)
    if here.rstrip().endswith("{"):
        if after is not None and len(_indent(after)) > len(_indent(here)):
            return _indent(after)
        return _indent(here) + _unit(lines)
    if not here.strip() or here.lstrip().startswith("#"):
        return _indent(after) if after is not None and not after.lstrip().startswith("#") else ""
    return _indent(here)


def scan_file(project: Project, marker: Marker, rel: str) -> list[Found]:
    path = project.abs(rel)
    if not path.is_file():
        return []
    try:
        tf = TextFile.read(path, project.encodings)
    except PsectError:
        return []
    found = []
    for i, line in enumerate(tf.lines):
        text = body(line)
        mark = marker.parse(text)
        if mark:
            found.append(Found(rel, i + 1, text, mark))
    return found


def _walk(project: Project) -> list[str]:
    out = []
    for folder, dirs, files in os.walk(project.root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.lower().endswith(project.extensions):
                out.append(project.rel(Path(folder) / name))
    return sorted(out)


def scan(project: Project, marker: Marker, files: set[str], everywhere: bool) -> list[Found]:
    targets = set(files) | (set(_walk(project)) if everywhere else set())
    found: list[Found] = []
    for rel in sorted(targets):
        found.extend(scan_file(project, marker, rel))
    return found


# --- add -------------------------------------------------------------------------


@dataclass
class AddResult:
    probe: Probe
    shown: list[str]
    warnings: list[str] = field(default_factory=list)


def add(
    project: Project,
    file: str,
    line: int,
    *,
    group: str | None = None,
    text: str | None = None,
    purpose: str = "",
    expect: str = "",
    by: str = "ai",
) -> AddResult:
    if group is not None and not GROUP_RE.match(group):
        raise PsectError(f"group must be letters, digits and _ . -: {group!r}")
    marker = Marker(project.prefix)
    ledger = Ledger(project.state_dir)
    path = Path(file)
    path = path if path.is_absolute() else project.root / path
    rel = project.rel(path)
    limit = int(project.settings["limits"]["active_probes"])
    probes = [p for p in ledger.active() if p.by == "ai" and p.group != BANNER_GROUP]
    if by == "ai" and group != BANNER_GROUP and len(probes) >= limit:
        raise PsectError(
            f"{limit} probes are already in (limits.active_probes); take some out first"
        )

    tf = TextFile.read(path, project.encodings)
    if not 0 <= line <= len(tf.lines):
        raise PsectError(f"{rel} has {len(tf.lines)} lines; no line {line}")
    index = line - 1  # the line the probe goes under; -1 = top of file

    warnings = []
    if index >= 0 and not ends_statement(body(tf.lines[index])):
        warnings.append(f"line {line} does not look like the end of a statement")

    seen = [f.mark.n for f in scan(project, marker, ledger.files() | {rel}, False) if f.mark.n]
    n = ledger.take_number(max(seen, default=0))
    ai = by == "ai"
    tag = marker.tag(ai, group, n)
    source = text if text is not None else project.template
    source = textwrap.dedent(source.strip("\n"))
    filled = source.replace("{tag}", tag).replace("{n}", str(n))
    filled = filled.replace("{where}", f"{path.name}:{line + 1}")

    pad = indent_for(tf.lines, index)
    in_macro = index >= 0 and body(tf.lines[index]).rstrip().endswith("\\")
    new = []
    for part in filled.split("\n"):
        content = pad + part if part.strip() else pad
        if in_macro:
            content = content.rstrip() + " \\"
        new.append(marker.mark_line(content, ai, group, n))

    tf.insert(index + 1, new)
    tf.write()

    probe = Probe(
        n=n,
        file=rel,
        line_hint=line + 1,
        lines=new,
        group=group,
        by=by,
        anchor=body(tf.lines[index]).strip() if index >= 0 else "",
        purpose=purpose,
        expect=expect,
        added=now(),
        function=enclosing_function([body(x) for x in tf.lines], index + 1),
    )
    ledger.add(probe)
    ledger.save(project.prefix)
    shown = []
    if index >= 0:
        shown.append(f"{rel}:{line}: {body(tf.lines[index])}")
    for i, content in enumerate(new):
        shown.append(f"{rel}:{line + 1 + i}: {content}")
    return AddResult(probe, shown, warnings)


# --- verify ----------------------------------------------------------------------


@dataclass
class Report:
    moved: list[dict[str, Any]] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)
    returned: list[int] = field(default_factory=list)
    orphans: list[dict[str, Any]] = field(default_factory=list)
    by_hand: list[dict[str, Any]] = field(default_factory=list)
    found: list[Found] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "moved": self.moved,
            "missing": self.missing,
            "returned": self.returned,
            "orphans": self.orphans,
            "by_hand": self.by_hand,
        }


def verify(project: Project, everywhere: bool = False, ledger: Ledger | None = None) -> Report:
    """Bring the ledger in line with the marks in the source."""
    marker = Marker(project.prefix)
    ledger = ledger or Ledger(project.state_dir)
    found = scan(project, marker, ledger.files(), everywhere)
    report = Report(found=found)
    by_n: dict[int, list[Found]] = {}
    for f in found:
        if f.mark.n is not None:
            by_n.setdefault(f.mark.n, []).append(f)

    for probe in ledger.probes.values():
        hits = [f for f in by_n.get(probe.n, []) if f.mark.ai == (probe.by == "ai")]
        if hits:
            first = hits[0]
            if probe.status != ACTIVE:
                report.returned.append(probe.n)
                probe.status = ACTIVE
            if (first.file, first.line) != (probe.file, probe.line_hint):
                report.moved.append(
                    {"n": probe.n, "from": f"{probe.file}:{probe.line_hint}",
                     "to": f"{first.file}:{first.line}"}
                )
                probe.file, probe.line_hint = first.file, first.line
        elif probe.status == ACTIVE:
            probe.status = MISSING
            report.missing.append(probe.n)

    for f in found:
        entry = {"file": f.file, "line": f.line, "n": f.mark.n, "group": f.mark.group,
                 "text": f.text}
        known = f.mark.n in ledger.probes and ledger.probes[f.mark.n].by == (
            "ai" if f.mark.ai else "user"
        )
        if known:
            continue
        (report.orphans if f.mark.ai else report.by_hand).append(entry)

    ledger.save(project.prefix)
    return report


# --- clear -----------------------------------------------------------------------


@dataclass
class ClearResult:
    removed: list[int] = field(default_factory=list)
    changed: list[int] = field(default_factory=list)  # edited by hand, left in
    skipped_files: list[str] = field(default_factory=list)
    left: int = 0


def clear(project: Project, group: str | None = None, orphans: bool = False) -> ClearResult:
    """Take out the probes an agent put in (optionally one group). Hand-made lines stay."""
    ledger = Ledger(project.state_dir)
    report = verify(project, ledger=ledger)
    result = ClearResult()
    wanted = [p for p in ledger.active(group) if p.by == "ai"]
    lines_by_n: dict[int, list[Found]] = {}
    for f in report.found:
        if f.mark.ai and f.mark.n is not None:
            lines_by_n.setdefault(f.mark.n, []).append(f)

    per_file: dict[str, set[int]] = {}
    for probe in wanted:
        hits = lines_by_n.get(probe.n, [])
        if [h.text for h in hits] != probe.lines:
            result.changed.append(probe.n)
            continue
        for h in hits:
            per_file.setdefault(h.file, set()).add(h.line - 1)
    orphan_ns = set()
    if orphans:
        for o in report.orphans:
            if group is None or o["group"] == group:
                per_file.setdefault(o["file"], set()).add(o["line"] - 1)
                orphan_ns.add(o["n"])

    done_files = set()
    for rel, indexes in sorted(per_file.items()):
        try:
            tf = TextFile.read(project.abs(rel), project.encodings)
            tf.delete(indexes)
            tf.write()
            done_files.add(rel)
        except PsectError:
            result.skipped_files.append(rel)

    for probe in wanted:
        if probe.n in result.changed:
            continue
        if all(h.file in done_files for h in lines_by_n.get(probe.n, [])):
            probe.status = REMOVED
            result.removed.append(probe.n)
    result.removed.extend(sorted(n for n in orphan_ns if n is not None))
    ledger.save(project.prefix)
    after = verify(project)
    result.left = len([f for f in after.found if f.mark.ai])
    return result


# --- note ------------------------------------------------------------------------


def note(project: Project, n: int, text: str | None, purpose: str | None,
         expect: str | None) -> Probe:
    ledger = Ledger(project.state_dir)
    probe = ledger.probes.get(n)
    if probe is None:
        raise PsectError(f"no probe #{n} in the ledger")
    if purpose is not None:
        probe.purpose = purpose
    if expect is not None:
        probe.expect = expect
    if text:
        probe.notes.append(f"{now()} {text}")
    ledger.save(project.prefix)
    return probe


def set_banner(project: Project, run: int, where: str, template: str | None = None) -> Probe:
    """Put the line that prints `[PSECT-RUN <run>]` at FILE:LINE, replacing the last one.

    A new build prints it when it starts, so its output is told from an old build's.
    """
    file, _, line = where.rpartition(":")
    if not file or not line.isdigit():
        raise PsectError(f"run.banner must be FILE:LINE, e.g. app/init.c:42 (got {where!r})")
    result = clear(project, BANNER_GROUP)
    if result.changed:
        raise PsectError(f"the run banner #{result.changed[0]} was edited by hand; take it out "
                         "yourself and run again")
    text = (template or BANNER_TEMPLATE).replace("{run}", str(run))
    text = text.replace("{prefix}", project.prefix)
    return add(project, file, int(line), group=BANNER_GROUP, text=text,
               purpose=f"run banner: run {run}").probe


def record_results(project: Project, run: int, hits: dict[int, Any]) -> list[Probe]:
    """Write what each probe in the source showed in `run` into the ledger.

    A probe with no hit gets a result too: "not reached" is a finding.
    """
    ledger = Ledger(project.state_dir)
    done = []
    for probe in ledger.active():
        if probe.group == BANNER_GROUP:
            continue
        hit = hits.get(probe.n)
        probe.results.append({
            "run": run,
            "at": now(),
            "hits": hit.hits if hit else 0,
            "first": hit.first if hit else None,
            "values": hit.values if hit else {},
            "samples": hit.samples[:2] if hit else [],
        })
        done.append(probe)
    ledger.save(project.prefix)
    return done
