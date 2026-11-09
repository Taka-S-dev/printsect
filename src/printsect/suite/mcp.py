"""`psect mcp`: printsect as an MCP server, over standard input and output.

An agent (Claude Code, or any MCP client) starts it as a child process:

    psect mcp --root <project>

Messages are JSON-RPC 2.0, one per line. Each tool runs the same command as the command
line, with --json, so an agent can do exactly what a person can, and nothing else.
The guide for agents (guide.md) goes out as the server's instructions.

What an agent sends is data, never options: arguments are checked against their
declared types, option values are passed as `--flag=value`, and values that sit in
a command's positions come after `--`. So a group named "--orphans" stays a name.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any, TextIO

from .. import __version__

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


GUIDES = {
    "loop": "how to find a bug: hypotheses, probes, runs, narrowing, cleaning up",
    "setup": "how to set a project up until doctor shows every part ok",
    "settings": "every settings key, commented, with made-up examples",
}


def guide(topic: str = "loop", lang: str = "en") -> str:
    """One of the guides for agents (and people): loop, setup or settings, in `lang`."""
    if topic == "settings":
        from ..core.config import init_template

        return ("# printsect.yaml\n\n" + init_template("init_config.yaml", lang)
                + "\n# printsect.local.yaml\n\n" + init_template("init_local.yaml", lang))
    name = {"loop": "guide", "setup": "guide_setup"}.get(topic)
    if name is None:
        raise ValueError(f"no guide {topic!r}: {', '.join(GUIDES)}")
    here = files(__package__)
    page = here.joinpath(f"{name}.{lang}.md")
    return (page if lang != "en" and page.is_file() else here.joinpath(f"{name}.md")).read_text(
        encoding="utf-8")


def instructions(lang: str = "en") -> str:
    """What an agent is told when it connects: the loop, and how to set a project up."""
    return guide("loop", lang) + "\n\n---\n\n" + guide("setup", lang)


@dataclass
class Tool:
    name: str
    description: str
    command: list[str]
    properties: dict[str, Any] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    options: dict[str, str] = field(default_factory=dict)  # property -> --flag (takes a value)
    switches: dict[str, str] = field(default_factory=dict)  # property -> --flag (on/off)
    positional: tuple[str, ...] = ()  # properties that go in the command's positions
    defaults: dict[str, Any] = field(default_factory=dict)

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {"type": "object", "properties": self.properties,
                            "required": list(self.required), "additionalProperties": False},
        }

    def argv(self, arguments: dict[str, Any], root: str) -> list[str]:
        a = {**self.defaults, **arguments}
        out = list(self.command)
        out += [f"{flag}={a[k]}" for k, flag in self.options.items() if a.get(k) not in (None, "")]
        out += [flag for k, flag in self.switches.items() if a.get(k) is True]
        out += ["--json", f"--root={root}"]
        values = [str(a[k]) for k in self.positional if a.get(k) not in (None, "")]
        return out + (["--", *values] if values else [])

    def problems(self, arguments: Any) -> list[str]:
        """What is wrong with the arguments an agent sent, by the declared schema."""
        if not isinstance(arguments, dict):
            return ["arguments must be an object"]
        found = [f"{k}: not an argument of {self.name}" for k in arguments
                 if k not in self.properties]
        found += [f"{k}: required" for k in self.required if arguments.get(k) in (None, "")]
        for k, v in arguments.items():
            want = self.properties.get(k, {}).get("type")
            ok = {
                "string": isinstance(v, str),
                "integer": isinstance(v, int) and not isinstance(v, bool),
                "number": isinstance(v, (int, float)) and not isinstance(v, bool),
                "boolean": isinstance(v, bool),
            }.get(want, True)
            allowed = self.properties.get(k, {}).get("enum")
            if not ok:
                found.append(f"{k}: expected {want}, got {type(v).__name__}")
            elif allowed and v not in allowed:
                found.append(f"{k}: one of {', '.join(allowed)}")
            elif isinstance(v, str) and ("\n" in v or "\r" in v or "\0" in v) and k != "text":
                found.append(f"{k}: one line, please")
        return found


def _s(desc: str) -> dict[str, Any]:
    return {"type": "string", "description": desc}


def _i(desc: str) -> dict[str, Any]:
    return {"type": "integer", "description": desc}


def _n(desc: str) -> dict[str, Any]:
    return {"type": "number", "description": desc}


def _b(desc: str) -> dict[str, Any]:
    return {"type": "boolean", "description": desc}


TOOLS = [
    Tool("run", "One turn: build, deploy, reproduce, read the console, count each probe's "
         "hits. Status waiting_user means a person must act first; then call with resume.",
         ["run"],
         {"resume": _b("carry on a run that stopped for a person"),
          "no_build": _b("use the outputs as they are"),
          "no_deploy": _b("the target already runs them")},
         switches={"resume": "--resume", "no_build": "--no-build", "no_deploy": "--no-deploy"}),
    Tool("hits", "What each probe printed in a run (default: the last).", ["hits"],
         {"run": _i("run number")}, positional=("run",)),
    Tool("probe_add", "Put a probe below LINE of FILE. Give purpose and expect. text is its "
         "C code; {tag} becomes the probe's tag, print name=value pairs after it.",
         ["probe", "add"],
         {"file": _s("source file, relative to the project"),
          "line": _i("the probe goes below this line (0 = top)"),
          "text": _s('C code, e.g. printf("[{tag}] len=%d\\n", len);'),
          "group": _s("a group name for related probes"),
          "purpose": _s("what this probe is to find out"),
          "expect": _s("what a hit or a miss would mean")},
         ("file", "line", "purpose"),
         options={"text": "--text", "group": "--group", "purpose": "--purpose",
                  "expect": "--expect"},
         positional=("file", "line")),
    Tool("probe_list", "The probes in the source, with purpose and last result.",
         ["probe", "list"], {"group": _s("only this group")}, positional=("group",)),
    Tool("probe_note", "Write what a probe showed or what was learned onto its record.",
         ["probe", "note"],
         {"n": _i("probe number"), "text": _s("the note"), "purpose": _s("new purpose"),
          "expect": _s("new expectation")}, ("n",),
         options={"purpose": "--purpose", "expect": "--expect"}, positional=("n", "text")),
    Tool("probe_clear", "Take out the agent's probes (all, or one group). Leaves files as "
         "they were and never touches lines a person put in.",
         ["probe", "clear"], {"group": _s("only this group")}, positional=("group",)),
    Tool("probe_verify", "Check the probe list against the source: moved, gone, unknown.",
         ["probe", "verify"]),
    Tool("deps_which", "Which outputs a source or header is built into, and how.",
         ["deps", "which"], {"file": _s("source or header, relative to the project")},
         ("file",), positional=("file",)),
    Tool("deps_impact", "What a change to a file makes again: objects, archives, outputs and "
         "where they are deployed.", ["deps", "impact"],
         {"file": _s("source or header, relative to the project"),
          "depth": _i("how many steps up to follow (default: all)")},
         ("file",), options={"depth": "--depth"}, positional=("file",)),
    Tool("deps_find", "Files of the build whose path matches (text, *.h, or a regex).",
         ["deps", "find"],
         {"pattern": _s("part of a path, or a glob"), "regex": _b("pattern is a regex")},
         ("pattern",), switches={"regex": "--regex"}, positional=("pattern",)),
    Tool("deps_tree", "Outputs -> archives -> sources, kept to matching or probed branches.",
         ["deps", "tree"],
         {"find": _s("keep branches with a matching file"),
          "probed": _b("only branches holding a probe (default when find is not given)")},
         options={"find": "--find"}, switches={"probed": "--probed"}),
    Tool("map", "The code's parts, who uses whom (most used first) and any cycles: read this "
         "first in code you do not know. Names only, no compiler; says how much it left out.",
         ["map"], {"top": _i("rows per list (default 30)"),
                   "by_dir": _b("group by folder even when the build's archives are known")},
         options={"top": "--top"}, switches={"by_dir": "--by-dir"}),
    Tool("map_focus", "One module, archive or folder: who uses it through which symbols (with "
         "file:line), what it uses, its files and which are entrances.",
         ["map", "focus"], {"module": _s("a module or archive from map, or any folder"),
                            "top": _i("rows per list (default 30)"),
                            "by_dir": _b("group by folder even when archives are known")},
         ("module",), options={"top": "--top"}, switches={"by_dir": "--by-dir"},
         positional=("module",)),
    Tool("map_who", "Who uses one function, variable or file: where it is defined, each using "
         "file with its lines, and why when the name has several definitions.",
         ["map", "who"], {"name": _s("a function or variable, or a source file"),
                          "top": _i("rows per list (default 30)"),
                          "by_dir": _b("group by folder even when archives are known")},
         ("name",), options={"top": "--top"}, switches={"by_dir": "--by-dir"},
         positional=("name",)),
    Tool("deps_scan", "Read the build: once before the deps_ tools, and again after a Makefile "
         "or deps settings changed.",
         ["deps"]),
    Tool("console_read", "Console lines after line `since` (pass the last number you saw).",
         ["console", "read"], {"since": _i("last line number already read (0 = all kept)")},
         options={"since": "--since"}),
    Tool("console_send", "Send a command to the target and return its answer. Ask before "
         "anything that resets or erases the target.",
         ["console", "send"], {"text": _s("the command"), "timeout": _n("seconds")},
         ("text",), options={"timeout": "--timeout"}, positional=("text",)),
    Tool("console_wait", "Wait until a console line matches a regex (after `since`, or from now).",
         ["console", "wait"],
         {"pattern": _s("regular expression"), "timeout": _n("seconds"),
          "since": _i("look after this line")}, ("pattern",),
         options={"timeout": "--timeout", "since": "--since"}, positional=("pattern",)),
    Tool("check", "Check printsect.yaml for every tool: unknown keys and wrong types.",
         ["check"]),
    Tool("doctor", "Try each part a run needs (settings, build reading, console, banner "
         "line, build and deploy commands) without building or touching the target. It "
         "runs the settings' deps and console commands only once a person approved them "
         "(psect approve); until then it names them. Each finding is ok, warn or ng with "
         "what to do next.", ["doctor"]),
    Tool("build_plan", "The build commands for the probes in now, in order, and the "
         "outputs to deploy. Shows them only; show them to the person before a first build. "
         "With all, as if every source held a probe; with file, as if that one did.",
         ["build", "--plan"],
         {"all": _b("plan as if every source held a probe"),
          "file": _s("plan as if this file held a probe")},
         options={"file": "--file"}, switches={"all": "--all"}),
    Tool("init", "Write printsect.yaml and printsect.local.yaml with every section "
         "commented. Never overwrites a file.", ["init"]),
    Tool("use", "The devices set up and the one in use; with name, choose one. Choose "
         "only the device the person named.",
         ["use"], {"name": _s("the device to use from now on")}, positional=("name",)),
    Tool("guide", "A guide: loop (finding a bug), setup (setting a project up), settings "
         "(every key with examples).",
         ["guide"], {"topic": {"type": "string", "enum": list(GUIDES),
                               "description": "loop, setup or settings"}},
         positional=("topic",)),
]
BY_NAME = {t.name: t for t in TOOLS}


# Tools that run commands written in the settings (build, deploy, deps, console), or
# answer from what such a command saved (the deps_ questions).
RUNS_SETTINGS = {"run", "deps_scan", "build_plan", "console_read", "console_send",
                 "console_wait", "deps_which", "deps_impact", "deps_find", "deps_tree"}


def _held_back(root: str) -> list[str]:
    """The settings commands not approved by a person, which an agent may not run."""
    from pathlib import Path

    from ..core.config import load_project
    from .approve import unapproved

    try:
        return unapproved(load_project(Path(root)))
    except Exception as exc:  # fail closed: what cannot be checked is not approved
        return [f"the settings could not be checked ({exc}); fix them, then psect approve"]


def call(tool: Tool, arguments: dict[str, Any], root: str) -> tuple[str, bool]:
    """Run the tool's command; (its JSON answer as text, whether it failed)."""
    from ..cli import main

    held = _held_back(root) if tool.name in RUNS_SETTINGS or tool.name == "doctor" else []
    if held and tool.name in RUNS_SETTINGS:
        listed = "\n".join(f"  {c}" for c in held)
        return (json.dumps({"error": "not approved", "commands": held}, ensure_ascii=False)
                + f"\nThese commands in the settings have not been approved by a person:\n"
                f"{listed}\nShow them to the person and ask them to run psect approve; "
                "you cannot approve them.", True)

    if tool.name == "deps_tree" and "probed" not in arguments and not arguments.get("find"):
        arguments = {**arguments, "probed": True}
    argv = tool.argv(arguments, root)
    if tool.name == "doctor" and held:
        argv.insert(1, "--no-commands")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):  # stdout belongs to the protocol
        code = main(argv)
    text = buf.getvalue().strip() or "{}"
    if code == 3:
        text += "\n(exit 3: a person has to act; tell them, then call again with resume)"
    return text, code in (1, 2)


def _reply(out: TextIO, msg_id: Any, result: Any = None,
           error: dict[str, Any] | None = None) -> None:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    out.write(json.dumps(msg, ensure_ascii=False) + "\n")
    out.flush()


def _lang(root: str) -> str:
    """The project's language (lang: in its settings, or PSECT_LANG), English if unsure."""
    from pathlib import Path

    from ..core.config import language, load_project

    try:
        return language(settings=load_project(Path(root)).settings)
    except Exception:  # a broken setting must not stop the agent from connecting
        return "en"


def _text(text: str, error: bool) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def handle(msg: dict[str, Any], root: str) -> tuple[Any, dict[str, Any] | None]:
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "printsect", "version": __version__},
            "instructions": instructions(_lang(root)),
        }, None
    if method == "ping":
        return {}, None
    if method == "tools/list":
        return {"tools": [t.schema() for t in TOOLS]}, None
    if method == "tools/call":
        tool = BY_NAME.get(params.get("name", ""))
        if tool is None:
            return None, {"code": -32602, "message": f"no tool {params.get('name')!r}"}
        args = params.get("arguments") or {}
        wrong = tool.problems(args)
        if wrong:
            return _text("; ".join(wrong), True), None
        return _text(*call(tool, args, root)), None
    return None, {"code": -32601, "message": f"no method {method!r}"}


def serve(root: str, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    """Answer requests until the client closes the input. `root` is the project, fixed."""
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _reply(stdout, None, error={"code": -32700, "message": "not JSON"})
            continue
        if not isinstance(msg, dict) or "method" not in msg:
            continue  # a response to us, which we never ask for
        if "id" not in msg:
            continue  # a notification (initialized, cancelled): nothing to answer
        try:
            result, error = handle(msg, root)
        except Exception as exc:  # one bad call must not end the session
            result, error = None, {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}
        _reply(stdout, msg["id"], result, error)
    return 0
