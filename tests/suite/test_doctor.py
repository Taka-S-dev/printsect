"""psect doctor: each part tried for real, with what to do when it is not ok."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from printsect.cli import main

DEMO = Path(__file__).parents[2] / "examples" / "demo"
needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="needs GNU make")


def findings(root, capsys) -> tuple[int, dict[str, dict[str, str]]]:
    capsys.readouterr()
    code = main(["doctor", "--json", "--root", str(root)])
    data = json.loads(capsys.readouterr().out)
    return code, {f["name"]: f for f in data["findings"]}


@pytest.fixture
def demo(tmp_path):
    root = tmp_path / "demo"
    shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns(".printsect", "build"))
    (root / "term.log").write_text("", encoding="utf-8")

    def setup(extra: str) -> Path:
        base = (DEMO / "printsect.yaml").read_text(encoding="utf-8")
        (root / "printsect.yaml").write_text(base + extra, encoding="utf-8")
        return root

    return setup


SET_UP = """
build:
  command: make {outs}
  artifacts: auto
run:
  banner: util/ring.c:5
deploy:
  type: manual
console:
  type: file
  path: term.log
"""


@needs_make
def test_a_project_set_up_for_run_is_all_ok(demo, capsys):
    code, f = findings(demo(SET_UP), capsys)
    assert code == 0
    assert {n: x["status"] for n, x in f.items()} == {
        "device": "--", "settings": "ok", "approval": "warn", "deps": "ok", "console": "ok",
        "banner": "ok", "build": "ok", "deploy": "ok", "reproduce": "--", "probes": "ok"}
    assert "ring_push()" in f["banner"]["message"]


def test_a_bare_project_says_what_to_add(tmp_path, capsys):
    (tmp_path / ".printsect").mkdir()
    code, f = findings(tmp_path, capsys)
    assert code == 1
    assert f["build"]["status"] == "ng" and "make {outs}" in f["build"]["hint"]
    assert f["console"]["status"] == "warn" and f["banner"]["status"] == "warn"
    assert f["deps"]["status"] == "warn"  # no Makefile: only matters with artifacts auto


@needs_make
@pytest.mark.parametrize("banner, words", [
    ("util/ring.c:1", "not inside a function"),
    ("util/ring.c:99", "no line 99"),
    ("util/nothere.c:3", "cannot read"),
    ("ring.c", "FILE:LINE"),
])
def test_a_bad_banner_place_is_named(demo, capsys, banner, words):
    code, f = findings(demo(SET_UP.replace("util/ring.c:5", banner)), capsys)
    assert code == 1 and f["banner"]["status"] == "ng" and words in f["banner"]["message"]


@needs_make
def test_a_console_that_does_not_answer(demo, capsys):
    cfg = SET_UP.replace("  type: file\n  path: term.log\n",
                         "  type: tcp\n  port: 9\n  read_request: {screen: true}\n")
    code, f = findings(demo(cfg), capsys)
    assert code == 1 and f["console"]["status"] == "ng"
    assert "running" in f["console"]["hint"]
    assert f["deploy"]["status"] == "warn"  # manual, but no console to see the banner


@needs_make
def test_steps_that_need_a_console_that_can_send(demo, capsys):
    code, f = findings(demo(SET_UP + "reproduce:\n  - send: app_start\n"), capsys)
    assert code == 1 and f["reproduce"]["status"] == "ng"
    assert "only reads" in f["reproduce"]["message"]


def test_doctor_changes_nothing(tmp_path, capsys):
    (tmp_path / ".printsect").mkdir()
    (tmp_path / "a.c").write_bytes(b"int f(void)\n{\n    return 0;\n}\n")
    (tmp_path / "printsect.yaml").write_text("run:\n  banner: a.c:3\n", encoding="utf-8")
    before = (tmp_path / "a.c").read_bytes()
    findings(tmp_path, capsys)
    assert (tmp_path / "a.c").read_bytes() == before
    assert not (tmp_path / ".printsect" / "runs").exists()
