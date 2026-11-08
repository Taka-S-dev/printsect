from __future__ import annotations

import json

import pytest

from printsect.cli import main
from printsect.core.errors import PsectError
from printsect.probe.cscan import enclosing_function
from printsect.probe.ledger import Ledger
from printsect.probe.marker import Marker
from printsect.probe.ops import add, clear, note, verify

SRC = b"""\
#include <stdio.h>

static int parse(int len)
{
    if (len == 0) {
        return -1;
    }
    return len;
}
"""


def read(project, name="a.c") -> str:
    return (project.root / name).read_bytes().decode()


def test_marker_parse_and_tag():
    m = Marker("PSECT")
    mark = m.parse('  printf("x"); /* PSECT-AI:len #7 */')
    assert mark and mark.ai and mark.group == "len" and mark.n == 7
    assert m.parse("x; /* PSECT #3 */").ai is False
    assert m.parse("x; // PSECT-AI #4").n == 4
    assert m.parse("x; /* PSECT-AI #5 */ \\").n == 5
    assert m.parse("x; /* not a mark */") is None
    assert m.tag(True, "len", 7) == "PSECT-AI 7 len"
    found = m.tag_re().search("boot [PSECT-AI 7 len] len=0")
    assert found and found.group("n") == "7"


def test_add_puts_line_below_with_indent_mark_and_ledger(project):
    p = project({"a.c": SRC})
    text = 'printf("[{tag}] len=%d\\n", len);'
    r = add(p, "a.c", 5, group="len", purpose="H1: len 0?", text=text)
    assert r.probe.n == 1
    assert r.probe.function == "parse"
    lines = read(p).splitlines()
    assert lines[5] == '        printf("[PSECT-AI 1 len] len=%d\\n", len); /* PSECT-AI:len #1 */'
    ledger = Ledger(p.state_dir)
    assert ledger.probes[1].purpose == "H1: len 0?"
    assert ledger.probes[1].line_hint == 6
    md = (p.state_dir / "PROBES.md").read_text(encoding="utf-8")
    assert "H1: len 0?" in md and "a.c:6" in md


def test_default_template_and_numbers_never_reused(project):
    p = project({"a.c": SRC})
    a = add(p, "a.c", 4).probe
    clear(p)
    b = add(p, "a.c", 4).probe
    assert (a.n, b.n) == (1, 2)
    assert '[PSECT-AI 2] %s:%d' in read(p)


def test_numbers_skip_marks_already_in_source(project):
    p = project({"a.c": SRC.replace(b"    return len;", b"    return len; /* PSECT #9 */")})
    assert add(p, "a.c", 4).probe.n == 10


def test_multiline_probe_keeps_inner_indent_and_one_number(project):
    p = project({"a.c": SRC})
    add(p, "a.c", 4, text="if (len > 3) {\n    printf(\"[{tag}]\\n\");\n}")
    lines = read(p).splitlines()
    assert lines[4] == "    if (len > 3) { /* PSECT-AI #1 */"
    assert lines[5] == '        printf("[PSECT-AI 1]\\n"); /* PSECT-AI #1 */'
    assert lines[6] == "    } /* PSECT-AI #1 */"
    clear(p)
    assert read(p) == SRC.decode()


def test_macro_continuation_keeps_backslash_last(project):
    src = b"#define F(x) \\\n    do { \\\n        g(x); \\\n    } while (0)\n"
    p = project({"m.h": src})
    add(p, "m.h", 3, text='printf("[{tag}]");')
    lines = read(p, "m.h").splitlines()
    assert lines[3] == '        printf("[PSECT-AI 1]"); /* PSECT-AI #1 */ \\'
    clear(p)
    assert read(p, "m.h") == src.decode()


def test_warns_inside_a_statement(project):
    p = project({"a.c": b"int x = f(1,\n          2);\n"})
    r = add(p, "a.c", 1)
    assert r.warnings


def test_clear_leaves_hand_made_and_hand_edited_lines(project):
    p = project({"a.c": SRC.replace(b"    return len;", b"    return len; /* PSECT #3 */")})
    add(p, "a.c", 4, group="g")
    add(p, "a.c", 9, group="g")
    text = read(p).replace("[PSECT-AI 5 g]", "[PSECT-AI 5 g] edited")
    (p.root / "a.c").write_bytes(text.encode())
    result = clear(p)
    assert result.removed == [4]
    assert result.changed == [5]
    assert "PSECT #3" in read(p)
    assert "edited" in read(p)
    assert result.left == 1


def test_clear_by_group(project):
    p = project({"a.c": SRC})
    add(p, "a.c", 4, group="a")
    add(p, "a.c", 4, group="b")
    clear(p, "a")
    assert "PSECT-AI:a" not in read(p) and "PSECT-AI:b" in read(p)


def test_verify_tracks_moves_missing_orphans(project):
    p = project({"a.c": SRC})
    add(p, "a.c", 4)
    add(p, "a.c", 9)
    text = read(p).splitlines(keepends=True)
    text.insert(0, "/* new header line */\n")
    text = [t for t in text if "#2 */" not in t]
    text.append("x; /* PSECT-AI:old #99 */\n")
    (p.root / "a.c").write_text("".join(text), encoding="utf-8", newline="")
    report = verify(p)
    assert report.moved and report.moved[0]["n"] == 1 and report.moved[0]["to"] == "a.c:6"
    assert report.missing == [2]
    assert [o["n"] for o in report.orphans] == [99]
    clear(p, orphans=True)
    assert "PSECT-AI" not in read(p)


def test_limit_on_active_probes(project):
    p = project({"a.c": SRC}, config="limits:\n  active_probes: 1\n")
    add(p, "a.c", 4)
    with pytest.raises(PsectError):
        add(p, "a.c", 4)


def test_custom_prefix(project):
    p = project({"a.c": SRC}, config="marker:\n  prefix: DBG\n")
    add(p, "a.c", 4, group="x")
    assert "/* DBG-AI:x #1 */" in read(p)


def test_note(project):
    p = project({"a.c": SRC})
    add(p, "a.c", 4)
    probe = note(p, 1, "H1 ruled out", purpose="H1", expect=None)
    assert probe.purpose == "H1" and probe.notes[0].endswith("H1 ruled out")


def test_enclosing_function_ignores_strings_and_comments():
    lines = ["/* { */", "int f(void)", "{", '  char *s = "}";', "  x();", "}", "int y;"]
    assert enclosing_function(lines, 4) == "f"
    assert enclosing_function(lines, 6) is None
    assert enclosing_function(["static int t[] = {", "1,", "};"], 1) is None


def test_cli_add_list_quickfix_clear(project, capsys):
    p = project({"a.c": SRC})
    root = str(p.root)
    assert main(["probe", "add", "a.c", "4", "-g", "g", "-p", "why", "--root", root]) == 0
    capsys.readouterr()
    assert main(["probe", "list", "--json", "--root", root]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["probes"][0]["purpose"] == "why"
    assert main(["probe", "list", "--quickfix", "--check", "--root", root]) == 1
    assert "a.c:5: [AI #1 g] why" in capsys.readouterr().out
    assert main(["probe", "clear", "--root", root]) == 0
    assert main(["probe", "list", "--check", "--root", root]) == 0


def test_ledger_paths_cannot_leave_the_project(project):
    p = project({"a.c": SRC})
    add(p, "a.c", 4)
    data = json.loads((p.state_dir / "probes.json").read_text(encoding="utf-8"))
    for bad in ("../outside.c", "/etc/x.c", "C:/x.c", "sub/../../x.c"):
        data["probes"][0]["file"] = bad
        (p.state_dir / "probes.json").write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(PsectError, match="outside the project"):
            clear(p)
    with pytest.raises(PsectError, match="outside the project"):
        p.abs("../x.c")
    assert p.abs("sub/../a.c") == p.root / "a.c"


def test_a_link_cannot_lead_outside_the_project(project, tmp_path_factory):
    import os

    outside = tmp_path_factory.mktemp("outside")
    (outside / "x.c").write_bytes(SRC)
    p = project({"a.c": SRC})
    try:
        os.symlink(outside, p.root / "linked", target_is_directory=True)
    except (OSError, NotImplementedError):
        try:  # Windows without the right to make links: a junction needs none
            import _winapi

            _winapi.CreateJunction(str(outside), str(p.root / "linked"))
        except (ImportError, OSError):
            pytest.skip("this system makes neither links nor junctions")
    with pytest.raises(PsectError, match="leads outside the project"):
        p.abs("linked/x.c")
    with pytest.raises(PsectError, match="outside the project"):
        add(p, "linked/x.c", 4)
    assert (outside / "x.c").read_bytes() == SRC
