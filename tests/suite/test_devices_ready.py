"""Devices, outputs made anew, and a target ready for commands."""

from __future__ import annotations

import json
import os
import sys

import pytest

from printsect.cli import main
from printsect.core.config import load_project
from printsect.core.errors import ConfigError
from printsect.probe.ops import add
from printsect.suite.build import plan

DEVICES = """
build:
  artifact: out/app.out
devices:
  board-a:
    build: {artifact_path: "out/a/{name}"}
  board-b:
    build: {artifact_path: "out/b/{name}"}
    deploy: {type: manual, message: "load {artifact_name} for {device}"}
"""


def test_the_chosen_device_lays_its_settings_over(project):
    p = project({}, config=DEVICES)
    assert p.device is None and p.devices == ("board-a", "board-b")
    with pytest.raises(ConfigError, match="none is chosen: psect use NAME"):
        p.require_device()
    b = load_project(p.root, device="board-b")
    assert b.settings["build"] == {"artifact": "out/app.out", "artifact_path": "out/b/{name}"}
    assert b.settings["deploy"]["type"] == "manual"
    with pytest.raises(ConfigError, match="no device 'board-c'"):
        load_project(p.root, device="board-c")


def test_one_device_is_used_without_choosing(project):
    p = project({}, config="devices:\n  only: {build: {artifact: x.out}}\n")
    assert p.device == "only" and p.settings["build"]["artifact"] == "x.out"


def test_psect_use_chooses_for_every_command(project, capsys, monkeypatch):
    p = project({}, config=DEVICES)
    root = ["--root", str(p.root)]
    assert main(["use", "board-b", *root]) == 0
    assert "* board-b" in capsys.readouterr().out
    assert load_project(p.root).device == "board-b"
    assert plan(load_project(p.root), 1).artifacts == ["out/b/app.out"]
    assert load_project(p.root, device="board-a").device == "board-a"  # --device wins
    monkeypatch.setenv("PSECT_DEVICE", "board-a")
    assert load_project(p.root).device == "board-a"  # and so does PSECT_DEVICE
    monkeypatch.delenv("PSECT_DEVICE")
    assert main(["use", "nope", *root]) == 1
    assert main(["use", "--clear", *root]) == 0
    assert load_project(p.root).device is None


def test_build_and_deploy_refuse_to_guess(project, capsys):
    p = project({}, config=DEVICES)
    assert main(["build", "--plan", "--root", str(p.root)]) == 2
    assert "none is chosen" in capsys.readouterr().err
    assert main(["deploy", "--root", str(p.root)]) == 2
    assert main(["deploy", "--json", "--device", "board-b", "--root", str(p.root)]) == 3
    assert "load app.out for board-b" in json.loads(capsys.readouterr().out)["message"]


def test_check_reads_every_device_and_doctor_names_the_choice(project, capsys):
    p = project({}, config=DEVICES + "  board-c:\n    biuld: {}\n")
    assert main(["check", "--root", str(p.root)]) == 2
    assert "devices.board-c: biuld: not a setting (did you mean build?)" in (
        capsys.readouterr().out)
    capsys.readouterr()
    main(["doctor", "--json", "--root", str(p.root)])
    found = {f["name"]: f for f in json.loads(capsys.readouterr().out)["findings"]}
    assert found["device"]["status"] == "ng" and "psect use" in found["device"]["hint"]


# -- a whole run: outputs made anew, and ready ------------------------------------------

SRC = b"int rx(int len)\n{\n    return len;\n}\n\nvoid init(void)\n{\n}\n"

BUILD = r'''
import re, sys
src, out = sys.argv[1], sys.argv[2]
lines = [m.group(1) for m in (re.search(r'printf\("(.*?)\\n"', l) for l in open(src)) if m]
open(out, "w").write("\n".join(lines) + "\n")
'''

TARGET = r'''
import sys
out, log = sys.argv[1], sys.argv[2]
with open(log, "a") as f:
    f.write("> load app.out\n")  # an old prompt, before the new build's banner
    for line in open(out).read().splitlines():
        f.write(line + "\n")
    f.write("booting...\n")
    f.write("> \n")  # the prompt of the new build: ready
'''


def loop_project(project, tmp_path, build_cmd, extra=""):
    (tmp_path / "build.py").write_text(BUILD, encoding="utf-8")
    (tmp_path / "target.py").write_text(TARGET, encoding="utf-8")
    log = tmp_path / "term.log"
    log.write_text("", encoding="utf-8")
    return project({"rx.c": SRC}, config=f"""
console: {{type: file, path: {json.dumps(str(log))}}}
build:
  steps: [{{run: {build_cmd}}}]
  artifact: app.out
deploy:
  type: command
  commands: ['{sys.executable} target.py app.out {log.as_posix()}']
  confirm_timeout: 5
run: {{banner: "rx.c:7"}}
stop: {{quiet: 1, timeout: 10}}
{extra}""")


def test_a_build_that_did_not_remake_the_output_stops_the_run(project, tmp_path, capsys):
    py = json.dumps(sys.executable)
    p = loop_project(project, tmp_path, f'[{py}, -c, "pass"]')  # "passes", makes nothing
    (p.root / "app.out").write_text("old\n", encoding="utf-8")
    os.utime(p.root / "app.out", (1_000_000_000, 1_000_000_000))
    assert main(["run", "--root", str(p.root)]) == 1
    assert "app.out (not updated)" in capsys.readouterr().out


def test_ready_waits_for_the_new_builds_prompt(project, tmp_path, capsys):
    py = json.dumps(sys.executable)
    good = loop_project(project, tmp_path, f"[{py}, build.py, rx.c, app.out]",
                        "run: {banner: 'rx.c:7', ready: '^>', ready_timeout: 5}\n")
    add(good, "rx.c", 2, text='printf("[{tag}]\\n");')
    assert main(["run", "--root", str(good.root)]) == 0
    out = capsys.readouterr().out
    assert "waiting for the target to be ready" in out and "run 1: ok at done" in out


def test_a_target_that_never_gets_ready_stops_the_run(project, tmp_path, capsys):
    py = json.dumps(sys.executable)
    p = loop_project(project, tmp_path, f"[{py}, build.py, rx.c, app.out]",
                     "run: {banner: 'rx.c:7', ready: 'login:', ready_timeout: 1}\n")
    assert main(["run", "--json", "--root", str(p.root)]) == 1
    answer = json.loads(capsys.readouterr().out)
    assert answer["stage"] == "ready" and "login:" in answer["message"]
