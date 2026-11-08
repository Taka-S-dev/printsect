"""Build rules: the commands come from the graph, in build order, never forgotten."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from printsect.cli import main
from printsect.core.config import load_project
from printsect.core.errors import ConfigError
from printsect.probe.ops import add
from printsect.suite.build import execute, plan

DEMO = Path(__file__).parents[2] / "examples" / "demo"
needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="needs GNU make")

RULES = """
build:
  steps:
    - run: make clean
    - for: archives
      run: make -C {made_in}
    - for: outs
      match: "*/netdrv.out"
      run: make {path} RUN={run}
  artifacts: auto
run:
  banner: util/ring.c:5
"""


@pytest.fixture
def demo(tmp_path):
    root = tmp_path / "demo"
    shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns(".printsect", "build"))

    def setup(extra: str):
        base = (DEMO / "printsect.yaml").read_text(encoding="utf-8")
        (root / "printsect.yaml").write_text(base + extra, encoding="utf-8")
        return load_project(root)

    return setup


@needs_make
def test_rules_follow_the_probes_in_build_order(demo):
    p = demo(RULES)
    add(p, "net/rx.c", 9)
    planned = plan(p, 7)
    assert planned.files == ["net/rx.c", "util/ring.c"]  # the probe, and the banner's file
    assert planned.archives == ["build/lib/libnet.a", "build/lib/libutil.a"]
    assert planned.outs == ["build/out/netdrv.out", "build/out/diskdrv.out"]
    assert [s.text() for s in planned.steps] == [
        "make clean",  # once, first: where it was written
        "make -C net",  # {made_in}: the folder the sub-make built it in
        "make -C util",
        "make build/out/netdrv.out RUN=7",  # match keeps only this output
    ]


@needs_make
def test_only_what_the_probes_touch_is_rebuilt(demo):
    p = demo(RULES.replace("banner: util/ring.c:5", "banner: disk/blk.c:6"))
    add(p, "disk/cache.c", 8)
    planned = plan(p, 1)
    assert planned.archives == ["build/lib/libdisk.a"]
    assert planned.outs == ["build/out/diskdrv.out"]


def test_rules_are_checked(project):
    p = project({}, config="build:\n  steps: [{run: x}]\n  command: y\n")
    with pytest.raises(ConfigError, match="steps or command, not both"):
        plan(p, 1)
    p = project({}, config="build:\n  steps: [{for: modules, run: x}]\n")
    with pytest.raises(ConfigError, match=r"steps\[0\]\.for: one of archives, outs, once"):
        plan(p, 1)
    p = project({}, config="build:\n  steps: [{for: outs, rnu: x}]\n")
    with pytest.raises(ConfigError, match="needs run"):
        plan(p, 1)


def test_steps_run_in_order_and_stop_at_the_first_failure(project, tmp_path):
    log = tmp_path / "order.txt"
    py = json.dumps(sys.executable)
    say = f"[{py}, -c, \"open('{log.as_posix()}', 'a').write('{{n}}')\"]"
    first, third = say.replace("{n}", "1"), say.replace("{n}", "3")
    p = project({}, config=(
        "build:\n  steps:\n"
        f"    - run: {first}\n"
        f"    - run: [{py}, -c, \"raise SystemExit('rx.c:3: error: oops')\"]\n"
        f"    - run: {third}\n"))
    planned = plan(p, 1)
    failed = execute(p, planned.steps, p.state_dir / "build.log", lambda s: None)
    assert failed is not None and "rx.c:3: error: oops" in failed
    assert log.read_text(encoding="utf-8") == "1"  # the third never ran
    assert "oops" in (p.state_dir / "build.log").read_text(encoding="utf-8")


@needs_make
def test_psect_build_plan_shows_without_running(demo, capsys):
    p = demo(RULES)
    assert main(["build", "--plan", "--json", "--root", str(p.root)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["steps"][0] == {"for": "once", "command": "make clean"}
    assert not (p.root / "build").exists()  # nothing was built


@needs_make
def test_the_whole_plan_can_be_seen_before_any_probe(demo, capsys):
    p = demo(RULES)
    assert plan(p, 1).files == ["util/ring.c"]  # no probes: only the banner's file
    whole = plan(p, 1, everything=True)
    assert whole.archives == [
        "build/lib/libnet.a", "build/lib/libutil.a", "build/lib/libdisk.a"]
    assert len(whole.steps) == 1 + 3 + 1  # clean, three archives, netdrv.out (match)
    one = plan(p, 1, files=["disk/blk.c"])
    assert one.archives == ["build/lib/libdisk.a"]
    assert main(["build", "--all", "--root", str(p.root)]) == 2  # only with --plan
    assert "add --plan" in capsys.readouterr().err


def test_doctor_asks_for_a_deploy_but_takes_none_as_meant(project, capsys):
    p = project({}, config="deploy:\n  type: none\n")
    main(["doctor", "--json", "--root", str(p.root)])
    found = {f["name"]: f for f in json.loads(capsys.readouterr().out)["findings"]}
    assert found["deploy"]["status"] == "ok" and "this PC" in found["deploy"]["message"]
    q = project({}, config="")
    (q.root / "printsect.yaml").write_text("version: 1\n", encoding="utf-8")
    main(["doctor", "--json", "--root", str(q.root)])
    found = {f["name"]: f for f in json.loads(capsys.readouterr().out)["findings"]}
    assert found["deploy"]["status"] == "warn" and "ask how" in found["deploy"]["hint"]
