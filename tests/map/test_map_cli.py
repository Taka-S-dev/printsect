"""The map as a whole: what counts as a use, modules, the commands, and running nothing."""

from __future__ import annotations

import json
import os

import pytest

from printsect.cli import main as psect
from printsect.map import index as index_module
from printsect.map.cli import main
from printsect.map.cycles import loops, round_trip
from printsect.map.index import FileEntry, Index
from printsect.map.model import Map, build, directory_groups
from printsect.suite.mcp import BY_NAME, RUNS_SETTINGS, call

NET = {
    "net/rx.c": b"#include <stdio.h>\nint rx(int n) { return ring_put(n) + tx(n); }\n",
    "net/tx.c": b"int tx(int n) { return ring_put(n); }\n",
    "util/ring.c": b"int ring_put(int n) { return n; }\nstatic int helper;\n",
    "app/main.c": b"int main(void) { return rx(0); }\n",
}


def data(capsys, *args):
    capsys.readouterr()
    code = main([*args, "--json"])
    return code, json.loads(capsys.readouterr().out)


def entry(*defs, refs=()):
    return FileEntry(defs=[(n, "f", static, 1) for n, static in defs], refs=list(refs))


def test_a_name_used_from_another_file_is_an_edge():
    t = build(Index(files={"a.c": entry(("f", False)), "b.c": entry(refs=["f", "printf"])}))
    assert t.edges == {("b.c", "a.c"): ["f"]}


def test_a_name_defined_in_two_files_is_left_out_and_counted():
    t = build(Index(files={"a.c": entry(("f", False)), "b.c": entry(("f", False)),
                           "c.c": entry(refs=["f"])}))
    assert t.edges == {}
    assert t.omitted["same_name_symbols"] == 1 and t.omitted["same_name_refs"] == 1


def test_a_static_elsewhere_cannot_be_what_a_file_means():
    t = build(Index(files={"a.c": entry(("f", True)), "c.c": entry(refs=["f"])}))
    assert t.edges == {} and t.omitted["static_refs"] == 1


def test_one_global_beside_statics_is_the_one_meant():
    t = build(Index(files={"a.c": entry(("f", True)), "b.c": entry(("f", False)),
                           "c.c": entry(refs=["f"])}))
    assert t.edges == {("c.c", "b.c"): ["f"]}


def test_files_the_build_does_not_use_are_left_out():
    index = Index(files={"win.c": entry(("f", False)), "unix.c": entry(("f", False)),
                         "c.c": entry(refs=["f"])})
    t = build(index, {"unix.c": "x", "c.c": "x"})
    assert t.edges == {("c.c", "unix.c"): ["f"]}
    assert t.omitted["not_in_build"] == 1


def test_a_heavy_folder_is_split_and_a_light_one_stays_whole():
    files = {}
    for i in range(30):
        files[f"big/heavy/f{i}.c"] = entry((f"s{i}", False))
        files[f"big/user{i}.c"] = entry(refs=[f"s{i}"])
    files["small/x.c"] = entry(refs=["s0"])
    groups = directory_groups(build(Index(files=files)))
    assert groups["big/heavy/f0.c"] == "big/heavy/"
    assert groups["big/user0.c"] == "big/"
    assert groups["small/x.c"] == "small/"


def test_overview_counts_roles_and_says_what_was_left_out(project, capsys):
    p = project(NET)
    code, out = data(capsys, "--root", str(p.root))
    assert code == 0
    rows = {m["name"]: m for m in out["modules"]["shown"]}
    assert rows["util/"]["role"] == "base" and rows["app/"]["role"] == "start"
    assert rows["net/"]["in"] == 1 and rows["net/"]["out"] == 2
    assert {"from": "net/", "to": "util/", "count": 2} in out["edges"]["shown"]
    assert out["grouped_by"] == "directory"
    assert "same_name_refs" in out["omitted"]


def test_focus_shows_both_sides_with_where_symbols_are(project, capsys):
    p = project(NET)
    _, out = data(capsys, "focus", "net", "--root", str(p.root))
    assert out["module"] == "net/" and out["files_in_module"] == 2
    used = out["from_outside"]["shown"][0]
    assert used["module"] == "app/"
    assert used["symbols"] == [{"name": "rx", "file": "net/rx.c", "line": 2}]
    assert out["to_outside"]["shown"][0]["module"] == "util/"
    assert out["inside"]["shown"][0]["from"] == "net/rx.c"
    files = {f["file"]: f for f in out["files"]["shown"]}
    assert files["net/rx.c"]["from_outside"] == 1 and files["net/tx.c"]["from_outside"] == 0


LOOP = {
    "a/one.c": b"int one(void) { return two(); }\n",
    "b/two.c": b"int two(void) { return three(); }\n",
    "c/three.c": b"int three(void) { return one(); }\n",
    "d/leaf.c": b"int leaf(void) { return one(); }\n",
}


def test_a_cycle_is_shown_with_a_way_round_and_its_symbols(project, capsys):
    p = project(LOOP)
    _, out = data(capsys, "--root", str(p.root))
    assert out["cycles"]["total"] == 1
    cycle = out["cycles"]["shown"][0]
    assert cycle["modules"]["shown"] == ["a/", "b/", "c/"]  # d/ only uses them
    legs = [(r["from"], r["to"]) for r in cycle["round_trip"]]
    assert legs == [("a/", "b/"), ("b/", "c/"), ("c/", "a/")]
    assert cycle["round_trip"][0]["symbols"] == [{"name": "two", "file": "b/two.c", "line": 1}]
    capsys.readouterr()
    main(["--root", str(p.root)])
    text = capsys.readouterr().out
    assert "3 module(s): a/, b/, c/" in text and "a/ -> b/  1  two (b/two.c:1)" in text


def test_no_cycle_says_so(project, capsys):
    p = project(NET)
    _, out = data(capsys, "--root", str(p.root))
    assert out["cycles"] == {"shown": [], "total": 0}
    capsys.readouterr()
    main(["--root", str(p.root)])
    assert "cycles: none" in capsys.readouterr().out


def test_two_cycles_are_told_apart_and_the_list_is_cut_by_top():
    files = {}
    for x, y in (("a", "b"), ("b", "a"), ("c", "d"), ("d", "c")):
        files[f"{x}/f.c"] = entry((f"{x}_f", False), refs=[f"{y}_f"])
    out = Map(build(Index(files=files))).overview(1)
    assert out["cycles"]["total"] == 2 and len(out["cycles"]["shown"]) == 1


def test_a_long_chain_does_not_reach_the_recursion_limit():
    uses = {str(i): [str(i + 1)] for i in range(5000)}
    uses["5000"] = ["0"]
    group = loops(uses)
    assert len(group) == 1 and len(group[0]) == 5001


def test_the_shortest_way_round_is_taken():
    uses = {"a": ["b", "c"], "b": ["d"], "c": ["a"], "d": ["a"]}
    assert round_trip("a", uses, {"a", "b", "c", "d"}) == ["a", "c"]


def test_who_gives_the_definition_and_each_user_with_lines(project, capsys):
    p = project(NET)
    _, out = data(capsys, "who", "ring_put", "--root", str(p.root))
    assert out["status"] == "one"
    assert out["defined"]["shown"] == [
        {"file": "util/ring.c", "line": 1, "kind": "function", "static": False}]
    rows = [(r["file"], r["module"], r["lines"]) for r in out["users"]["shown"]]
    assert rows == [("net/rx.c", "net/", [2]), ("net/tx.c", "net/", [1])]
    capsys.readouterr()
    main(["who", "ring_put", "--root", str(p.root)])
    text = capsys.readouterr().out
    assert "util/ring.c:1  function" in text and "net/rx.c:2  net/" in text


def test_who_lists_uses_inside_the_defining_file_after_the_others(project, capsys):
    p = project({"a.c": b"int f(void) { return 0; }\nint g(void) { return f(); }\n",
                 "b.c": b"int h(void) { return f(); }\n"})
    _, out = data(capsys, "who", "f", "--root", str(p.root))
    rows = [(r["file"], r["own_file"], r["lines"]) for r in out["users"]["shown"]]
    assert rows == [("b.c", False, [1]), ("a.c", True, [2])]


def test_who_skips_comments_strings_and_dead_code(project, capsys):
    source = (b'// f here\nchar *s = "f";\n#if 0\nint x = f();\n#endif\n'
              b"int use(void) { return f(); }\n")
    p = project({"a.c": b"int f(void) { return 0; }\n", "b.c": source})
    _, out = data(capsys, "who", "f", "--root", str(p.root))
    assert out["users"]["shown"][0]["lines"] == [6]


def test_who_says_why_a_name_with_two_definitions_is_not_settled(project, capsys):
    p = project({"a/x.c": b"int f(void) { return 0; }\n", "b/x.c": b"int f(void) { return 1; }\n",
                 "c/y.c": b"int g(void) { return f(); }\n"})
    _, out = data(capsys, "who", "f", "--root", str(p.root))
    assert out["status"] == "same_name" and out["defined"]["total"] == 2
    assert [r["file"] for r in out["users"]["shown"]] == ["c/y.c"]
    capsys.readouterr()
    main(["who", "f", "--root", str(p.root)])
    assert "cannot be told" in capsys.readouterr().out


def test_who_a_static_is_used_only_in_its_own_file(project, capsys):
    p = project({"a.c": b"static int f(void) { return 0; }\nint g(void) { return f(); }\n",
                 "b.c": b"int h(void) { return f(); }\n"})
    _, out = data(capsys, "who", "f", "--root", str(p.root))
    assert out["status"] == "static" and out["cannot_reach"] == 1
    assert [(r["file"], r["lines"]) for r in out["users"]["shown"]] == [("a.c", [2])]


def test_who_a_name_with_no_definition_lists_its_users(project, capsys):
    p = project({"a.c": b"int f(void) { return printf(0); }\n"})
    _, out = data(capsys, "who", "printf", "--root", str(p.root))
    assert out["status"] == "undefined" and out["users"]["total"] == 1


def test_who_a_definition_not_used_in_its_file_is_not_a_user(project, capsys):
    p = project({"a.c": b"int f(void) { return 0; }\n"})
    _, out = data(capsys, "who", "f", "--root", str(p.root))
    assert out["users"] == {"shown": [], "total": 0}


def test_who_cuts_files_and_lines(project, capsys):
    p = project({"a.c": b"int f(void) { return 0; }\n",
                 "b.c": b"int g(void) {\n" + b"f();\n" * 12 + b"return 0; }\n",
                 "c.c": b"int h(void) { return f(); }\n"})
    _, out = data(capsys, "who", "f", "--top", "1", "--root", str(p.root))
    row = out["users"]["shown"][0]
    assert out["users"]["total"] == 2 and len(out["users"]["shown"]) == 1
    assert len(row["lines"]) == 10 and row["more_lines"] == 2


def test_who_a_file_lists_what_each_user_takes_from_it(project, capsys):
    p = project(NET)
    _, out = data(capsys, "who", "net/rx.c", "--root", str(p.root))
    assert out["kind"] == "file" and out["module"] == "net/" and out["defines"] == 1
    assert out["users"]["shown"][0]["file"] == "app/main.c"
    assert out["users"]["shown"][0]["symbols"][0]["name"] == "rx"
    _, by_name = data(capsys, "who", "ring.c", "--root", str(p.root))
    assert by_name["name"] == "util/ring.c"


def test_who_an_unclear_or_unknown_name_stops(project, capsys):
    p = project({"a/x.c": b"int foo_one;\n", "b/x.c": b"int two;\n"})
    assert main(["who", "x.c", "--root", str(p.root)]) == 1
    assert "a/x.c, b/x.c" in capsys.readouterr().err
    assert main(["who", "foo_on", "--root", str(p.root)]) == 1
    assert "foo_one" in capsys.readouterr().err
    assert main(["who", "nothing.c", "--root", str(p.root)]) == 1


def test_who_works_with_an_index_that_gives_no_lines():
    t = build(Index(files={"a.c": entry(("f", False)), "b.c": entry(refs=["f"])}))
    out = Map(t).symbol("f", 10)
    assert [(r["file"], r["lines"]) for r in out["users"]["shown"]] == [("b.c", [])]


def test_an_unknown_module_names_the_nearest(project, capsys):
    p = project(NET)
    assert main(["focus", "nett", "--root", str(p.root)]) == 1
    assert "net/" in capsys.readouterr().err


def test_top_cuts_the_list_and_says_how_much(project, capsys):
    p = project(NET)
    _, out = data(capsys, "--top", "1", "--root", str(p.root))
    assert len(out["edges"]["shown"]) == 1 and out["edges"]["total"] == 2
    main(["--top", "1", "--root", str(p.root)])
    assert "1 more" in capsys.readouterr().out


def test_only_changed_files_are_read_again(project, capsys, monkeypatch):
    p = project(NET)
    main(["--root", str(p.root)])
    read = []
    original = index_module.scan
    monkeypatch.setattr(index_module, "scan", lambda text: read.append(text) or original(text))
    main(["--root", str(p.root)])
    assert read == []
    (p.root / "app" / "main.c").write_bytes(b"int main(void) { return tx(0); }\n")
    os.utime(p.root / "app" / "main.c", ns=(1, 1))
    _, out = data(capsys, "--root", str(p.root))
    assert len(read) == 1
    assert {"from": "app/", "to": "net/", "count": 1} in out["edges"]["shown"]
    (p.root / "app" / "main.c").unlink()
    _, out = data(capsys, "--root", str(p.root))
    assert out["files"] == 3


def test_a_damaged_saved_index_is_read_again_from_the_source(project, capsys):
    p = project(NET)
    main(["--root", str(p.root)])
    (p.root / ".printsect" / "map.json").write_text('{"fingerprint": 1}', encoding="utf-8")
    _, out = data(capsys, "--root", str(p.root))
    assert out["files"] == 4


@pytest.mark.parametrize("files", [
    '{"bad.c": {"m": 1, "s": 1, "r": "", "d": [["only two"]], "x": 0, "b": 0}}',
    '{"bad.c": {"m": 1, "s": 1, "r": "x", "d": [["ok; rm", "f", 0, 1]], "x": 0, "b": 0}}',
    '{"bad.c": {"m": 1, "s": 1, "r": "esc\\u001b[2J", "d": [], "x": 0, "b": 0}}',
    '{"bad.c": {"m": 1, "s": 1, "r": "", "d": [], "x": "no", "b": 0}}',
])
def test_a_saved_index_that_is_not_ours_is_thrown_away(project, capsys, files):
    p = project(NET)
    main(["--root", str(p.root)])
    saved = json.loads((p.root / ".printsect" / "map.json").read_text(encoding="utf-8"))
    saved["files"] = json.loads(files)
    (p.root / ".printsect" / "map.json").write_text(json.dumps(saved), encoding="utf-8")
    _, out = data(capsys, "--root", str(p.root))
    assert out["files"] == 4


def test_a_link_in_the_state_folder_stops_the_map(project, capsys, monkeypatch):
    from printsect.core import fsutil

    p = project(NET)
    monkeypatch.setattr(fsutil, "plain_or_absent", lambda *paths: False)
    assert main(["--root", str(p.root)]) == 2
    assert "link" in capsys.readouterr().err
    assert not (p.root / ".printsect" / "map.json").exists()


def test_plain_or_absent_refuses_links_and_devices(tmp_path, monkeypatch):
    import stat as st

    from printsect.core import fsutil

    assert fsutil.plain_or_absent(tmp_path, tmp_path / "missing")
    for mode in (st.S_IFLNK, st.S_IFIFO, st.S_IFCHR):
        fake = os.stat_result((mode | 0o644, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        monkeypatch.setattr(fsutil.os, "lstat", lambda path, fake=fake: fake)
        assert not fsutil.plain_or_absent(tmp_path)


def test_a_file_name_with_control_characters_is_left_out(project, capsys):
    p = project(NET)
    try:
        (p.root / "net" / "a\x1b[2Jb.c").write_bytes(b"int esc;\n")
    except OSError:
        pytest.skip("this file system does not allow such a name")
    capsys.readouterr()
    assert main(["--root", str(p.root)]) == 0
    assert "\x1b" not in capsys.readouterr().out


def test_links_are_not_followed(project, capsys, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.c").write_bytes(b"int secret(void) { return 0; }\n")
    p = project(NET)
    try:
        os.symlink(outside, p.root / "linked", target_is_directory=True)
    except OSError:
        pytest.skip("this PC does not let us make a link")
    _, out = data(capsys, "--root", str(p.root))
    assert out["files"] == 4 and out["omitted"]["links"] == 1


def test_a_shift_jis_comment_ending_in_a_backslash_byte(project, capsys):
    # The second byte of this character is the backslash; read as bytes it would
    # continue the comment onto the next line and hide the definition.
    p = project({"a.c": "// 表\nint defined_here(void) { return 0; }\n".encode("cp932")})
    _, out = data(capsys, "focus", ".", "--root", str(p.root))
    assert out["files_in_module"] == 1
    saved = json.loads((p.root / ".printsect" / "map.json").read_text(encoding="utf-8"))
    assert saved["files"]["a.c"]["d"][0][0] == "defined_here"


def test_folders_starting_with_a_dot_and_excluded_paths_are_skipped(project, capsys):
    p = project({**NET, ".hidden/x.c": b"int x;\n", "third/y.c": b"int y;\n"},
                config="map:\n  exclude: ['third/*']\n")
    _, out = data(capsys, "--root", str(p.root))
    assert out["files"] == 4


def test_what_map_exclude_took_out_is_counted(project, capsys):
    extra = {"third/y.c": b"int y;\n", "third/z.txt": b"x", "vendor/in/v.c": b"int v;\n"}
    p = project({**NET, **extra}, config="map:\n  exclude: ['third/*', 'vendor']\n")
    _, out = data(capsys, "--root", str(p.root))
    assert out["files"] == 4 and out["omitted"]["excluded"] == 2  # a file and a folder
    capsys.readouterr()
    main(["--root", str(p.root)])
    assert "by map.exclude 2 path(s)" in capsys.readouterr().out


def test_definitions_inside_an_if_are_counted_and_reported(project, capsys):
    p = project({"a.c": b"int plain;\n#ifdef X\nint maybe;\n#else\nint other;\n#endif\n"
                        b"#if 1\nint sure;\n#endif\n#if 0\nint dead;\n#endif\n"})
    _, out = data(capsys, "--root", str(p.root))
    assert out["inside_if"] == 2
    capsys.readouterr()
    main(["--root", str(p.root)])
    assert "#if may leave them out: 2 definition(s)" in capsys.readouterr().out
    _, again = data(capsys, "--root", str(p.root))  # from the saved index
    assert again["inside_if"] == 2


def test_no_definition_inside_an_if_says_nothing(project, capsys):
    p = project(NET)
    capsys.readouterr()
    main(["--root", str(p.root)])
    assert "#if" not in capsys.readouterr().out


def test_a_misspelt_setting_stops_with_the_nearest(project, capsys):
    p = project(NET, config="map:\n  exlude: []\n")
    assert main(["--root", str(p.root)]) == 2
    assert "exclude" in capsys.readouterr().err


def test_no_c_files_says_so(project, capsys):
    p = project({"notes.txt": b"x"})
    assert main(["--root", str(p.root)]) == 0
    assert "no C source files" in capsys.readouterr().out


def test_a_plugin_can_supply_the_index(project, capsys, monkeypatch):
    from printsect.core import plugins

    class Mine:
        def __init__(self, settings):
            pass

        def index(self, project, log):
            return Index(files={"p.c": entry(("f", False)), "q.c": entry(refs=["f"])})

    monkeypatch.setitem(plugins._builtin.setdefault("indexer", {}), "mine", Mine)
    p = project({}, config="map:\n  type: mine\n")
    _, out = data(capsys, "--root", str(p.root))
    assert out["files"] == 2


def deps_json(p):
    graph = {
        "version": 1, "generated": "", "command": "", "inputs": {}, "warnings": [],
        "headers_traced": True,
        "objects": {"o/rx.o": {"source": "net/rx.c"}, "o/tx.o": {"source": "net/tx.c"},
                    "o/ring.o": {"source": "util/ring.c"}},
        "archives": {"lib/net.a": {"objects": ["o/rx.o", "o/tx.o"]},
                     "lib/util.a": {"objects": ["o/ring.o"]}},
        "outs": {},
    }
    (p.root / ".printsect" / "deps.json").write_text(json.dumps(graph), encoding="utf-8")


def test_psect_map_groups_by_archive_and_leaves_out_what_is_not_built(project, capsys):
    p = project(NET)
    deps_json(p)
    capsys.readouterr()
    assert psect(["map", "--json", "--root", str(p.root)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["grouped_by"] == "archive"
    assert {"from": "lib/net.a", "to": "lib/util.a", "count": 2} in out["edges"]["shown"]
    assert out["omitted"]["not_in_build"] == 1  # app/main.c


@pytest.mark.parametrize("graph", [
    {"objects": {"o.o": {"source": "a\x1b[2J.c"}}, "archives": {"l.a": {"objects": ["o.o"]}}},
    {"objects": {"o.o": {"source": "a.c"}}, "archives": {"l\x07.a": {"objects": ["o.o"]}}},
    {"objects": {"o.o": {"source": ["not", "text"]}}, "archives": {}},
    {"objects": {"o.o": "not a mapping"}, "archives": {}},
])
def test_a_saved_build_that_is_not_ours_falls_back_to_folders(project, capsys, graph):
    p = project(NET)
    data_on_disk = {"version": 1, "outs": {}, **graph}
    (p.root / ".printsect" / "deps.json").write_text(json.dumps(data_on_disk), encoding="utf-8")
    capsys.readouterr()
    assert psect(["map", "--json", "--root", str(p.root)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["grouped_by"] == "directory" and out["files"] == 4


def test_psect_map_by_dir_keeps_folders(project, capsys):
    p = project(NET)
    deps_json(p)
    capsys.readouterr()
    assert psect(["map", "--by-dir", "--json", "--root", str(p.root)]) == 0
    assert json.loads(capsys.readouterr().out)["grouped_by"] == "directory"


def test_psect_map_marks_probes(project, capsys, monkeypatch):
    t = build(Index(files={"a/x.c": entry(("f", False)), "b/y.c": entry(refs=["f"])}))
    m = Map(t, None, {"a/x.c": [{"n": 7}]})
    rows = {r["name"]: r for r in m.overview(10)["modules"]["shown"]}
    assert rows["a/"]["probes"] == 1
    assert m.focus("a/", 10)["files"]["shown"][0]["probes"] == [7]


def test_the_agent_can_use_it_without_approval(project):
    p = project(NET)
    assert {"map", "map_focus", "map_who"} <= set(BY_NAME)
    assert not {"map", "map_focus", "map_who"} & RUNS_SETTINGS
    text, is_error = call(BY_NAME["map_who"], {"name": "ring_put"}, str(p.root))
    assert not is_error, text
    assert json.loads(text)["status"] == "one"
    text, is_error = call(BY_NAME["map_focus"], {"module": "net"}, str(p.root))
    assert not is_error, text
    assert json.loads(text)["module"] == "net/"
