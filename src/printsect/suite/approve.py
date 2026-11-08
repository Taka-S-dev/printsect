"""Commands in the settings run for an agent only once a person approved them.

An agent may write printsect.yaml itself (the setup guide asks it to). Without a gate,
writing a command into the settings and calling a tool that runs it would let the
agent run anything. So what the settings would have run is approved as a whole: the
deps, build, deploy, console and reproduce sections exactly as written (commands with
their form, folders, everything), and the Makefiles make -n reads (it still runs
$(shell ...)). Changing any of it needs approving again.

`psect approve` is for a person at a terminal only: it shows the commands with any
control characters made visible, asks, and is never offered to an agent. The record is
kept outside the project, in the user's own settings folder, and signed with a key
kept there, so it cannot arrive with a project or be written by editing project files.
An agent that can write anywhere as the user could still forge one deliberately; this
stops it from happening by mistake or by a trick within the project.

On the command line a person runs what they asked for, as with make.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import os
import pathlib
import re
import unicodedata
from pathlib import Path
from typing import Any

from ..core.config import Project
from ..core.fsutil import write_atomic
from ..core.timeutil import now

# What the settings may run, sections whole.
SECTIONS = ("deps", "build", "deploy", "console", "reproduce")


def _store() -> pathlib.Path:
    """The user's own printsect folder (never inside a project)."""
    if os.environ.get("PSECT_CONFIG_DIR"):
        return pathlib.Path(os.environ["PSECT_CONFIG_DIR"])
    if os.name == "nt" and os.environ.get("APPDATA"):
        return pathlib.Path(os.environ["APPDATA"]) / "printsect"
    base = os.environ.get("XDG_CONFIG_HOME") or str(pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "printsect"


def _key(create: bool) -> bytes | None:
    path = _store() / "approval.key"
    try:
        return path.read_bytes()
    except OSError:
        if not create:
            return None
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


def visible(text: str) -> str:
    """`text` with control and format characters shown as escapes, so what is displayed
    is what runs (no terminal codes, carriage returns or hidden marks)."""
    return "".join(
        ch if unicodedata.category(ch)[0] != "C" else f"\\x{ord(ch):02x}" if ord(ch) < 256
        else f"\\u{ord(ch):04x}"
        for ch in text
    )


def _ftp_transport(deploy: dict[str, Any], root: Path) -> str:
    from ..deploy.ftp import verifies

    if not deploy.get("tls"):
        return "unencrypted"
    if not verifies(deploy):
        return "tls, certificate NOT checked"
    shown, _ = _trust(deploy, root)
    return f"tls, certificate checked against {shown}" if shown else "tls, certificate checked"


def _trust(deploy: Any, root: Path) -> tuple[str | None, str | None]:
    """The certificate trusted for an ftp target (tls_ca), by content, as (what is
    shown, what is signed: the full hash). It may be outside the project, where no other
    part of the approval looks."""
    from ..core.errors import ConfigError
    from ..deploy.ftp import read_ca

    if not isinstance(deploy, dict) or deploy.get("type") != "ftp":
        return None, None
    try:
        ca = read_ca(deploy, root)
    except ConfigError as exc:
        return str(exc), f"refused: {exc}"
    if ca is None:
        return None, None
    path, data = ca
    full = hashlib.sha256(data).hexdigest()
    return f"{path} sha256 {full[:16]}", full


def _text(command: Any) -> str:
    return " ".join(str(w) for w in command) if isinstance(command, list) else str(command)


# Not covered: what the agent edits (C and C++ sources and headers, where probes go)
# and what the build makes for the target. Fixed here, so settings cannot widen them;
# anything a project adds (approval_ignore) is in the deps section, approved itself.
# Things the host may run or load (.pyc, .so, .dll) are covered.
SOURCE_EXTS = frozenset({".c", ".cc", ".cpp", ".cxx", ".c++", ".h", ".hh", ".hpp", ".hxx"})
OUTPUT_EXTS = frozenset({".o", ".obj", ".a", ".lib", ".out", ".elf", ".axf", ".bin", ".hex",
                         ".srec", ".map", ".lst"})
VCS_DIRS = frozenset({".git", ".svn", ".hg"})
# printsect's own state that changes as it works (runs, graph, probe list, pages).
OWN_STATE = frozenset({"runs", "deps.json", "DEPS.md", "deps.html", "probes.json",
                       "PROBES.md", "build.log", "device", ".gitignore"})
# What make sees as one line: CRLF made LF, and a newline joined to the next line only
# after an odd run of backslashes (an even run is literal backslashes).
_CONTINUATION = re.compile(rb"(?<!\\)((?:\\\\)*)\\\n")


def _make_lines(data: bytes) -> list[bytes]:
    return _CONTINUATION.sub(rb"\1 ", data.replace(b"\r\n", b"\n")).split(b"\n")


# A generated .d dependency file is left out only while every line has the shape a
# compiler writes: `targets: prerequisites`, nothing else. Anything that does not fit,
# or any stray backslash, and the file is covered.
_DEP_LINE = re.compile(rb"[^\s:=!;$#\t\\][^:=!;$#\t\\]*:[^:=!;$#\t\\]*")
_DIRECTIVES = frozenset(b"include sinclude -include define endef export unexport override "
                        b"private vpath ifeq ifneq ifdef ifndef else endif load -load "
                        b"undefine".split())


def _plain_dependencies(path: pathlib.Path) -> bool:
    try:
        lines = _make_lines(path.read_bytes())
    except OSError:
        return False
    for line in lines:
        if not line.strip():
            continue
        first = line.split(None, 1)[0].rstrip(b":")
        if (line.startswith(b"\t") or first in _DIRECTIVES
                or not _DEP_LINE.fullmatch(line)):
            return False
    return True


# A source or header is left out (probes go there) unless make could read it: it holds
# make's expansion syntax, or a covered file names it on an `include` line.
_MAKE_SYNTAX = re.compile(rb"\$[({]")
_INCLUDE_WORDS = re.compile(rb"(?:^|[\s(])-?s?include\s+([^#]*)")


def _make_like(path: pathlib.Path) -> bool:
    try:
        return bool(_MAKE_SYNTAX.search(path.read_bytes()))
    except OSError:
        return True  # unreadable: not left out


def _included_names(paths: list[pathlib.Path]) -> set[str]:
    """File names on `include` lines of these files, case-folded."""
    names: set[str] = set()
    for path in paths:
        try:
            lines = _make_lines(path.read_bytes())
        except OSError:
            continue
        for line in lines:
            for words in _INCLUDE_WORDS.findall(line):
                for word in words.split():
                    text = word.decode("utf-8", "replace").replace("\\", "/")
                    names.add(text.rsplit("/", 1)[-1].casefold())
    return names


def _file_hash(path: pathlib.Path) -> bytes:
    """A file's content hash, read afresh every time (a decision rests on it)."""
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.digest()


def _covered(project: Project, deps: dict[str, Any]) -> tuple[str, str]:
    """Every file of the project but C sources and target outputs, by content: anything
    make might read (Makefiles, what they include however they include it, scripts) is
    in it, with no parsing to get wrong. Linked folders are followed (and the link
    itself counted). Returns (a summary, the full digest)."""
    ignore = [str(g) for g in deps.get("approval_ignore") or []]
    root = project.root.resolve()
    h = hashlib.sha256()
    seen: set[str] = set()
    covered: list[tuple[str, pathlib.Path]] = []
    sources: list[tuple[str, pathlib.Path]] = []  # exempt unless a covered file includes them
    for folder, dirs, files in os.walk(root, followlinks=True):
        here = pathlib.Path(folder)
        real = os.path.realpath(here)
        if real in seen:  # a link back into what was already walked
            dirs[:] = []
            continue
        seen.add(real)
        rel_folder = here.relative_to(root).as_posix()
        keep = []
        for d in sorted(dirs):
            if d in VCS_DIRS or (rel_folder == ".printsect" and d in OWN_STATE):
                continue
            if (here / d).is_symlink():
                link = f"{rel_folder}/{d} -> {os.readlink(here / d)}".encode()
                h.update(b"link\0" + len(link).to_bytes(8, "big") + link)
            keep.append(d)
        dirs[:] = keep
        for f in sorted(files):
            path = here / f
            rel = path.relative_to(root).as_posix()
            ext = path.suffix.lower()
            if ext in OUTPUT_EXTS:  # binary target outputs
                continue
            if rel_folder == ".printsect" and f in OWN_STATE:
                continue
            if any(fnmatch.fnmatch(rel, g) for g in ignore):
                continue
            if ext == ".d" and _plain_dependencies(path):
                continue
            (sources if ext in SOURCE_EXTS else covered).append((rel, path))
    # Sources make could read join the covered files; follow what they include in turn.
    left = [(rel, path) for rel, path in sources if not _make_like(path)]
    covered += [(rel, path) for rel, path in sources if _make_like(path)]
    reading = [path for _, path in covered]
    while reading:
        included = _included_names(reading)
        promoted = [(rel, path) for rel, path in left if path.name.casefold() in included]
        left = [item for item in left if item not in promoted]
        covered += promoted
        reading = [path for _, path in promoted]
    count = 0
    for rel, path in sorted(covered):
        try:
            content = _file_hash(path)
        except OSError:
            content = b"(unreadable)"
        name = rel.encode("utf-8")
        h.update(len(name).to_bytes(8, "big") + name + content)
        count += 1
    full = h.hexdigest()
    return f"{count} file(s), sha256 {full[:16]}", full


def _outside(project: Project, deps: dict[str, Any]) -> list[str]:
    """Paths in deps.command that lead outside the project: not covered, so shown."""
    words = deps.get("command") or []
    words = words if isinstance(words, list) else str(words).split()
    run_in = (project.root / str(deps.get("cwd", "."))).resolve()
    found = []
    for w in (str(x) for x in words):
        for flag in ("-f", "-C", "-I", "--file=", "--makefile=", "--directory=",
                     "--include-dir="):
            if w.startswith(flag) and len(w) > len(flag):
                w = w[len(flag):]
                break
        if w.startswith("-") or not any(c in w for c in "/\\") and not w.startswith(".."):
            continue
        target = (run_in / w).resolve()
        if not target.is_relative_to(project.root.resolve()):
            found.append(str(target))
    return found


def commands(project: Project) -> list[str]:
    """What the settings would have run, for a person to read (not what is signed).

    Anything not in the expected shape is listed as it is rather than skipped.
    """
    s = project.settings
    found: list[str] = []

    def section(name: str) -> dict[str, Any]:
        value = s.get(name)
        if value is None:
            return {}
        if not isinstance(value, dict):
            found.append(f"{name}: {value!r} (not a mapping)")
            return {}
        return value

    def each(where: str, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        found.append(f"{where}: {value!r} (not a list)")
        return []

    deps = section("deps")
    kind = deps.get("type", "make")
    found.append(f"deps ({kind}): {_text(deps.get('command') or 'make -n -B -w')} "
                 f"(in {deps.get('cwd', '.')})")
    if kind == "make":
        found.append("project files make may read (all but C sources and build "
                     f"outputs): {_covered(project, deps)[0]}")
        run_in = (project.root / str(deps.get("cwd", "."))).resolve()
        if not run_in.is_relative_to(project.root.resolve()):
            found.append(f"deps.cwd is outside the project ({run_in}): files there are "
                         "not covered")
        found += [f"outside the project, not covered: {o}" for o in _outside(project, deps)]
    for key in ("approval_ignore",):  # cwd is on the first line
        if deps.get(key) is not None:
            found.append(f"deps.{key}: {deps[key]!r}")
    build = section("build")
    for i, step in enumerate(each("build.steps", build.get("steps"))):
        if isinstance(step, dict) and set(step) <= {"for", "run", "match"} and "run" in step:
            found.append(f"build.steps[{i}] ({step.get('for', 'once')}): {_text(step['run'])}")
        else:
            found.append(f"build.steps[{i}]: {step!r}")
    if build.get("command") is not None:
        found.append(f"build.command: {_text(build['command'])}")
    if build.get("cwd") is not None:
        found.append(f"build.cwd: {build['cwd']}")
    deploy = section("deploy")
    if deploy.get("type") not in (None, "none", "manual"):
        found.append(f"deploy.type: {deploy.get('type')}")
        for c in each("deploy.commands", deploy.get("commands")) + (
                [deploy["command"]] if deploy.get("command") is not None else []):
            found.append(f"deploy: {_text(c)}")
        if deploy.get("host") is not None:
            user = deploy.get("user", "anonymous")
            secret = ("no password" if user == "anonymous" else
                      f"password from ${deploy.get('password_env', 'PSECT_FTP_PASSWORD')}")
            found.append(f"deploy: put to {user}@{deploy['host']}:{deploy.get('port', 21)} "
                         f"{deploy.get('path', '{artifact_name}')} ({secret}, "
                         f"{_ftp_transport(deploy, project.root)})")
        for c in each("deploy.after", deploy.get("after")):
            found.append(f"deploy.after (sent to the target): {_text(c)}")
    console = section("console")
    if console.get("type") not in (None, "tcp", "serial", "file"):
        found.append(f"console.type: {console.get('type')}")
        for key in ("read", "send"):
            if console.get(key) is not None:
                found.append(f"console.{key}: {_text(console[key])}")
    for i, step in enumerate(each("reproduce", s.get("reproduce"))):
        if isinstance(step, dict) and "send" in step:
            found.append(f"reproduce[{i}] sends to the target: {_text(step['send'])}")
    return [visible(c) for c in found]


def digest(project: Project) -> str:
    """What is signed: the sections exactly as written, the device, and the Makefiles."""
    raw = {name: project.settings.get(name) for name in SECTIONS}
    deps = raw["deps"] if isinstance(raw["deps"], dict) else {}
    canon = {
        "root": str(project.root),
        "device": project.device,
        "settings": raw,
        "files": _covered(project, deps)[1],
        "trust": _trust(raw["deploy"], project.root)[1],
    }
    text = json.dumps(canon, ensure_ascii=False, sort_keys=True, default=repr)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _records() -> dict[str, Any]:
    try:
        data = json.loads((_store() / "approvals.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _mac(key: bytes, root: str, d: str) -> str:
    return hmac.new(key, f"{root}\0{d}".encode(), hashlib.sha256).hexdigest()


def unapproved(project: Project) -> list[str]:
    """The commands an agent may not run yet: all of them, unless a person approved this
    exact set for this project. Empty once approved."""
    d = digest(project)
    key = _key(create=False)
    entry = _records().get(str(project.root), {}).get(d) if key else None
    if isinstance(entry, dict) and key and hmac.compare_digest(
            str(entry.get("mac", "")), _mac(key, str(project.root), d)):
        return []
    return commands(project)


def approve(project: Project, expected: str) -> list[str]:
    """Record, signed, that a person approved what they were shown: `expected` is the
    digest taken before asking. If anything changed since, nothing is approved."""
    listed = commands(project)
    d = digest(project)
    if not hmac.compare_digest(d, expected):
        from ..core.errors import PsectError

        raise PsectError("the settings or Makefiles changed while you were asked; nothing "
                         "was approved: run psect approve again")
    key = _key(create=True)
    assert key is not None
    data = _records()
    mine = data.setdefault(str(project.root), {})
    mine[d] = {"at": now(), "device": project.device, "commands": listed,
               "mac": _mac(key, str(project.root), d)}
    _store().mkdir(parents=True, exist_ok=True)
    write_atomic(_store() / "approvals.json", json.dumps(data, ensure_ascii=False, indent=2)
                 + "\n")
    return listed
