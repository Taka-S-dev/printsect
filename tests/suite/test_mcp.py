"""psect mcp as a client sees it: a child process speaking JSON-RPC, one line each."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from printsect.cli import build_parser
from printsect.suite.mcp import TOOLS

SRC = b"int rx(int len)\n{\n    return len;\n}\n"


def talk(root, *messages):
    """Send messages to a fresh server; its answers by id."""
    text = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)
    done = subprocess.run([sys.executable, "-m", "printsect", "mcp", "--root", str(root)],
                          input=text.encode("utf-8"), capture_output=True, timeout=60)
    answers = [json.loads(line) for line in done.stdout.decode("utf-8").splitlines() if line]
    return {a["id"]: a for a in answers}, done


def call(i, name, **arguments):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


def test_a_session_from_hello_to_a_probe(project):
    p = project({"rx.c": SRC})
    answers, done = talk(
        p.root,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        call(3, "probe_add", file="rx.c", line=2, purpose="lenは0か",
             text='printf("[{tag}] len=%d\\n", len);'),
        call(4, "probe_list"),
        call(5, "deps_which", file="nowhere.c"),
        call(6, "no_such_tool"),
        call(7, "probe_add", file="rx.c"),
        {"jsonrpc": "2.0", "id": 8, "method": "no/such"},
        {"jsonrpc": "2.0", "id": 9, "method": "ping"},
    )
    assert done.returncode == 0, done.stderr
    init = answers[1]["result"]
    assert init["protocolVersion"] == "2025-06-18"
    assert init["serverInfo"]["name"] == "printsect"
    assert "probe_clear" in init["instructions"]  # the agent guide goes along
    assert len(answers) == 9  # the notification got no answer

    names = {t["name"] for t in answers[2]["result"]["tools"]}
    assert {"run", "hits", "probe_add", "console_read", "deps_which"} <= names

    added = answers[3]["result"]
    assert added["isError"] is False
    assert json.loads(added["content"][0]["text"])["probe"]["purpose"] == "lenは0か"
    assert "PSECT-AI" in (p.root / "rx.c").read_text(encoding="utf-8")

    listed = json.loads(answers[4]["result"]["content"][0]["text"])
    assert listed["probes"][0]["purpose"] == "lenは0か"
    assert answers[5]["result"]["isError"] is True  # not built into anything: exit 1
    assert answers[6]["error"]["code"] == -32602
    assert answers[7]["result"]["isError"] is True  # line and purpose missing
    assert answers[8]["error"]["code"] == -32601
    assert answers[9]["result"] == {}


def test_an_unknown_protocol_version_gets_the_newest():
    answers, _ = talk(".", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "1999-01-01"}})
    assert answers[1]["result"]["protocolVersion"] == "2025-06-18"


SAMPLE = {"file": "rx.c", "line": 2, "text": "x", "group": "g", "purpose": "p", "expect": "e",
          "n": 1, "pattern": "p", "regex": True, "find": "rx", "probed": True, "since": 3,
          "timeout": 1.5, "run": 2, "resume": True, "no_build": True, "no_deploy": True,
          "topic": "setup", "name": "board-a", "all": True, "module": "net", "top": 5, "depth": 1,
          "by_dir": True}


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
def test_every_tool_is_a_command_line_psect_accepts(tool):
    for arguments in ({}, {k: SAMPLE[k] for k in tool.properties}):
        if not all(k in arguments for k in tool.required):
            continue
        args = build_parser().parse_args(tool.argv(arguments, "."))  # SystemExit on a mismatch
        assert args.json is True and args.root == "."


@pytest.mark.parametrize("tool", [t for t in TOOLS if t.positional], ids=lambda t: t.name)
def test_values_that_look_like_options_stay_values(tool):
    sneaky = {k: ("--orphans" if tool.properties[k]["type"] == "string" else 1)
              for k in tool.properties}
    enums = [k for k, v in tool.properties.items() if "enum" in v]
    if enums:  # a value outside the list never reaches the command line
        assert any("one of" in p for p in tool.problems(sneaky))
        return
    args = vars(build_parser().parse_args(tool.argv(sneaky, ".")))
    assert args["root"] == "." and args.get("orphans") in (None, False)
    for k in tool.positional:
        if tool.properties[k]["type"] == "string":
            assert "--orphans" in args.values()


def test_option_values_cannot_start_an_option():
    tool = next(t for t in TOOLS if t.name == "probe_add")
    argv = tool.argv({"file": "a.c", "line": 1, "purpose": "--root=/elsewhere"}, ".")
    args = build_parser().parse_args(argv)
    assert args.purpose == "--root=/elsewhere" and args.root == "."


def test_arguments_are_checked_against_their_types(project):
    p = project({"rx.c": SRC})
    answers, _ = talk(
        p.root,
        call(1, "probe_add", file="rx.c", line="2", purpose="x"),
        call(2, "probe_clear", group="g", orphans=True),
        call(3, "run", resume="yes"),
        call(4, "deps_which", file="a.c\n--root=/"),
    )
    assert "line: expected integer" in answers[1]["result"]["content"][0]["text"]
    assert "orphans: not an argument" in answers[2]["result"]["content"][0]["text"]
    assert "resume: expected boolean" in answers[3]["result"]["content"][0]["text"]
    assert "one line" in answers[4]["result"]["content"][0]["text"]
    assert all(a["result"]["isError"] for a in answers.values())


def test_a_group_named_like_an_option_does_not_clear_orphans(project):
    # An agent mark the list does not know (#9) sits in a file the list does know, which
    # is what `clear --orphans` would take out. A group name must not turn that on.
    p = project({"rx.c": SRC.replace(b"    return len;", b"    return len; /* PSECT-AI #9 */")})
    talk(p.root, call(1, "probe_add", file="rx.c", line=2, purpose="keeps rx.c listed"))
    answers, _ = talk(p.root, call(2, "probe_clear", group="--orphans"))
    text = (p.root / "rx.c").read_text(encoding="utf-8")
    assert "PSECT-AI #9" in text  # the orphan is left alone
    assert "PSECT-AI #10" in text  # and so is the probe: no group is called "--orphans"


def test_an_agent_gets_what_it_needs_to_set_a_project_up(project):
    p = project({"rx.c": SRC})
    answers, _ = talk(
        p.root,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        call(3, "init"),
        call(4, "guide", topic="settings"),
        call(5, "doctor"),
        call(6, "build_plan"),
        call(7, "use"),
        call(8, "guide", topic="nonsense"),
    )
    instructions = answers[1]["result"]["instructions"]
    assert "## The loop" in instructions and "## Steps" in instructions  # both guides
    assert "Ask, never guess" in instructions
    names = {t["name"] for t in answers[2]["result"]["tools"]}
    assert {"init", "doctor", "build_plan", "use", "guide", "check"} <= names
    assert (p.root / "printsect.yaml").is_file() and (p.root / "printsect.local.yaml").is_file()
    settings = json.loads(answers[4]["result"]["content"][0]["text"])["text"]
    assert "build:" in settings and "console:" in settings and "devices:" in settings
    findings = json.loads(answers[5]["result"]["content"][0]["text"])["findings"]
    assert {f["name"] for f in findings} >= {"deps", "console", "banner", "build"}
    # reading the build runs make -n: not before a person approved it
    assert answers[6]["result"]["isError"] is True
    assert "psect approve" in answers[6]["result"]["content"][0]["text"]
    assert json.loads(answers[7]["result"]["content"][0]["text"])["devices"] == []
    assert answers[8]["result"]["isError"] is True  # not one of the topics


def test_the_guides_read_by_name():
    from printsect.suite.mcp import GUIDES, guide

    for topic in GUIDES:
        assert len(guide(topic)) > 500
    assert "made-up" in guide("settings")
