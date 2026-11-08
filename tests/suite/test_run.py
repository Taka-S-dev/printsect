"""The whole loop on this PC: a made-up build and target stand in for the real ones.

The "build" reads the probe and banner printf lines out of the C source into app.out;
"deploying" makes the "target" print them to its console log, as a loaded module
would. Lines with SKIP stand for code the target never reaches.
"""

from __future__ import annotations

import json
import re
import sys

import pytest

from printsect.cli import main
from printsect.probe.ledger import Ledger
from printsect.probe.ops import add

SRC = b"""\
int rx(int len)
{
    if (len <= 0) {
        return -1;
    }
    return len;
}

void init(void)
{
}
"""

BUILD = r'''
import re, sys
src, out, run = sys.argv[1], sys.argv[2], sys.argv[3]
lines = []
for line in open(src, encoding="utf-8"):
    m = re.search(r'printf\("(.*?)\\n"', line)
    if m:
        lines.append(m.group(1).replace("%d", "0"))
if run == "fail":
    sys.exit("rx.c:3: error: expected ';'")
open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
'''

TARGET = r'''
import sys
out, log = sys.argv[1], sys.argv[2]
with open(log, "a", encoding="utf-8") as f:
    f.write("> load app.out\n")
    for line in open(out, encoding="utf-8").read().splitlines():
        if "SKIP" not in line:
            f.write(line + "\n")
    f.write("> \n")
'''


@pytest.fixture
def loop(project, tmp_path):
    (tmp_path / "build.py").write_text(BUILD, encoding="utf-8")
    (tmp_path / "target.py").write_text(TARGET, encoding="utf-8")
    log = tmp_path / "term.log"
    log.write_text("old output\n", encoding="utf-8")
    py = json.dumps(sys.executable)

    def make(extra=""):
        return project({"rx.c": SRC}, config=f"""
console: {{type: file, path: {json.dumps(str(log))}}}
build:
  command: [{py}, build.py, rx.c, app.out, "{{run}}"]
  artifact: app.out
deploy:
  type: command
  commands: ['{sys.executable} target.py app.out {log}']
  confirm_timeout: 5
run: {{banner: "rx.c:10"}}
stop: {{quiet: 1, timeout: 10}}
{extra}""")

    return make, log


def test_one_turn_counts_hits_and_records_them(loop, capsys):
    make, _ = loop
    p = make()
    add(p, "rx.c", 3, group="rx", text='printf("[{tag}] len=%d\\n", len);', purpose="len 0?")
    add(p, "rx.c", 6, group="rx", text='printf("[{tag}] SKIP ok\\n");', purpose="reached?")
    assert main(["run", "--root", str(p.root)]) == 0
    out = capsys.readouterr().out
    assert "run 1: ok at done" in out
    assert re.search(r"#1 +1 hit .*rx.c:4", out)
    assert "len = 0" in out
    assert re.search(r"#2 +not reached", out)

    probes = Ledger(p.state_dir).probes
    assert probes[1].results[-1]["hits"] == 1 and probes[1].results[-1]["values"] == {"len": ["0"]}
    assert probes[2].results[-1]["hits"] == 0
    summary = json.loads((p.state_dir / "runs" / "1" / "summary.json").read_text(encoding="utf-8"))
    assert summary["banner_seen"] is True and summary["not_reached"] == [2]
    banner = [x for x in probes.values() if x.group == "run"][0]
    assert "PSECT-RUN 1" in banner.lines[0]

    assert main(["hits", "--root", str(p.root)]) == 0
    assert "run 1" in capsys.readouterr().out


def test_the_banner_moves_on_with_each_run(loop, capsys):
    make, _ = loop
    p = make()
    add(p, "rx.c", 3, group="rx", text='printf("[{tag}]\\n");')
    assert main(["run", "--root", str(p.root)]) == 0
    capsys.readouterr()
    assert main(["run", "--json", "--root", str(p.root)]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["run"] == 2 and second["banner_seen"] is True
    banners = [x for x in Ledger(p.state_dir).active() if x.group == "run"]
    assert len(banners) == 1 and "PSECT-RUN 2" in banners[0].lines[0]
    assert (p.root / "rx.c").read_text(encoding="utf-8").count("PSECT-RUN") == 1
    assert Ledger(p.state_dir).probes[1].results[-1]["run"] == 2


def test_a_failed_build_stops_and_shows_the_error(loop, capsys):
    make, _ = loop
    p = make()
    cfg = (p.root / "printsect.yaml").read_text(encoding="utf-8").replace('"{run}"', "fail")
    (p.root / "printsect.yaml").write_text(cfg, encoding="utf-8")
    assert main(["run", "--root", str(p.root)]) == 1
    out = capsys.readouterr().out
    assert "build failed" in out and "rx.c:3: error" in out


def test_a_step_for_a_person_stops_and_resumes(loop, capsys):
    make, _ = loop
    p = make("reproduce:\n  - ask: Unplug the cable\n  - sleep: 0.1\n")
    add(p, "rx.c", 3, group="rx", text='printf("[{tag}]\\n");')
    assert main(["run", "--json", "--root", str(p.root)]) == 3  # no terminal: hand it over
    answer = json.loads(capsys.readouterr().out)
    assert answer["status"] == "waiting_user" and "Unplug the cable" in answer["message"]
    assert main(["run", "--resume", "--root", str(p.root)]) == 0
    assert re.search(r"#1 +1 hit", capsys.readouterr().out)


def test_settings_for_the_run_are_checked(project, capsys):
    p = project({}, config="stop:\n  quite: 5\nrun:\n  baner: x.c:1\n")
    assert main(["check", "--root", str(p.root)]) == 2
    out = capsys.readouterr().out
    assert "stop.quite: not a setting (did you mean quiet?)" in out
    assert "run.baner: not a setting (did you mean banner?)" in out
