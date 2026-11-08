from __future__ import annotations

import re

from printsect.probe.hits import count, numbered
from printsect.probe.ops import add

SRC = b"""int rx(int len)
{
    if (len <= 0) {
        return -1;
    }
    return len;
}
"""


def test_hits_counts_values_and_keeps_a_few():
    log = "\n".join([
        "[PSECT-AI 3 rx] len=0 state=1",
        "noise",
        "[PSECT-AI 3 rx] len=0 state=2 [PSECT-AI 4] x=9",
        *[f"[PSECT-AI 3 rx] len={i}" for i in range(1, 8)],
        "[PSECT 5] hand-made",
        "[PSECT-RUN 2]",
    ])
    hits = count(numbered(log), "PSECT")
    assert hits[3].hits == 9 and hits[3].first == 1 and hits[3].last == 10
    assert hits[3].values["len"] == ["0", "1", "2", "3", "4"] and hits[3].more_values
    assert hits[3].values["state"] == ["1", "2"]
    assert hits[4].values == {"x": ["9"]}  # the second tag on a line keeps its own values
    assert 5 in hits and len(hits) == 3  # hand-made counts; the run banner is not a probe
    assert 5 not in count(numbered(log), "PSECT", ai_only=True)


def test_psect_probe_hits_reads_any_log(project, tmp_path, capsys):
    from printsect.probe.cli import main as probe_main

    p = project({"rx.c": SRC})
    add(p, "rx.c", 3, text='printf("[{tag}]\\n");')
    add(p, "rx.c", 6, text='printf("[{tag}]\\n");')
    log = tmp_path / "saved.log"
    log.write_text("[PSECT-AI 1] len=4\n", encoding="utf-8")
    assert probe_main(["hits", str(log), "--root", str(p.root)]) == 0
    out = capsys.readouterr().out
    assert re.search(r"#1 +1 hit", out) and re.search(r"#2 +not reached", out)
