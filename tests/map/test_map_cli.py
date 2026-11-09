"""The map as a whole: what counts as a use, modules, the commands, and running nothing."""

from __future__ import annotations

import json
import os

import pytest

from printsect.cli import main as psect
from printsect.map import index as index_module
from printsect.map.analysis import issues
from printsect.map.cli import main
from printsect.map.cycles import loops, round_trip
from printsect.map.index import FileEntry, Index
from printsect.map.model import Map, build, classify, directory_groups
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
    assert "header's macro" in out["not_seen"]  # none found is not none there
    capsys.readouterr()
    main(["who", "f", "--root", str(p.root)])
    assert "not seen: calls through a function pointer" in capsys.readouterr().out


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


CHAIN = {
    "app/main.c": b"int main(void) { return mid(); }\n",
    "svc/mid.c": b"int mid(void) { return low(); }\n",
    "drv/low.c": b"int low(void) { return 0; }\n",
    "drv/other.c": b"int other(void) { return 0; }\n",
}


def test_impact_goes_level_by_level(project, capsys):
    p = project(CHAIN)
    _, out = data(capsys, "impact", "low", "--root", str(p.root))
    rows = [(r["file"], r["depth"]) for r in out["files"]["shown"]]
    assert rows == [("svc/mid.c", 1), ("app/main.c", 2)]
    assert out["symbol"] == "low" and out["modules"] == {"svc/": 1, "app/": 1}
    _, near = data(capsys, "impact", "low", "--depth", "1", "--root", str(p.root))
    assert near["files"]["total"] == 1


def test_impact_up_shows_what_it_rests_on(project, capsys):
    p = project(CHAIN)
    _, out = data(capsys, "impact", "app/main.c", "--up", "--root", str(p.root))
    assert [r["file"] for r in out["files"]["shown"]] == ["svc/mid.c", "drv/low.c"]
    assert out["direction"] == "up"


def test_impact_under_psect_says_what_the_build_makes_again(project, capsys):
    p = project(NET)
    deps_json(p)
    capsys.readouterr()
    assert psect(["map", "impact", "net/rx.c", "--json", "--root", str(p.root)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["build"]["objects"] == ["o/rx.o"] and out["build"]["archives"] == ["lib/net.a"]


def test_impact_of_a_name_with_no_one_definition_stops(project, capsys):
    p = project({"a.c": b"static int f(void) { return 0; }\n"})
    assert main(["impact", "f", "--root", str(p.root)]) == 1
    assert "no one definition" in capsys.readouterr().err


def test_who_finds_what_is_defined_at_a_line(project, capsys):
    p = project({"a.c": b"int one(void) { return 0; }\n\nint two(void) {\n  return 0;\n}\n"})
    _, out = data(capsys, "who", "a.c:4", "--root", str(p.root))
    assert out["name"] == "two" and out["at"] == "a.c:4"
    assert main(["who", "a.c:0", "--root", str(p.root)]) == 1


def test_who_says_how_sure_and_can_give_editor_links(project, capsys):
    p = project(NET)
    _, out = data(capsys, "who", "ring_put", "--links", "--root", str(p.root))
    assert out["confidence"] == "likely"
    link = out["users"]["shown"][0]["link"]
    assert link.startswith("vscode://file/") and link.endswith("net/rx.c:2")


def test_a_compiler_backed_index_can_say_confirmed():
    t = build(Index(files={"a.c": entry(("f", False)), "b.c": entry(refs=["f"])}, confirmed=True))
    assert Map(t).symbol("f", 5)["confidence"] == "confirmed"
    assert Map(t).confidence()["confirmed"] == 1


CLASSES = {**NET, "vendor/v.c": b"int v;\n", "gen/g.c": b"int g;\n", "tests/t.c": b"int t;\n"}


def test_files_get_a_class_and_a_project_can_add_its_own():
    found = classify(["vendor/a.c", "gen/b.c", "x/tests/c.c", "net/d.c", "e.c"],
                     {"comm": ["net/*"]})
    assert found == {"vendor/a.c": "third_party", "gen/b.c": "generated", "x/tests/c.c": "test",
                     "net/d.c": "comm", "e.c": "product"}


def test_a_filter_takes_classes_out_and_says_so(project, capsys):
    p = project(CLASSES)
    _, out = data(capsys, "--hide", "third_party,test", "--root", str(p.root))
    assert out["files"] == 5 and out["hidden"] == {"third_party": 1, "test": 1}
    _, only = data(capsys, "--only", "generated", "--root", str(p.root))
    assert only["files"] == 1 and only["hidden"]["product"] == 4
    assert main(["--only", "nope", "--root", str(p.root)]) == 1


def test_overview_counts_uses_by_how_sure(project, capsys):
    p = project(NET)
    _, out = data(capsys, "--root", str(p.root))
    assert out["confidence"]["likely"] == 4 and out["confidence"]["ambiguous"] == 0
    capsys.readouterr()
    main(["--root", str(p.root)])
    assert "uses by certainty: likely 4" in capsys.readouterr().out


def test_grouping_by_output_needs_the_build(project, capsys):
    p = project(NET)
    assert psect(["map", "--by-out", "--root", str(p.root)]) == 1
    assert "psect deps" in capsys.readouterr().err
    assert main(["--by-out", "--root", str(p.root)]) == 1


def test_grouping_by_output(project, capsys):
    p = project(NET)
    graph = {"version": 1,
             "objects": {"o/r.o": {"source": "net/rx.c"}, "o/t.o": {"source": "util/ring.c"}},
             "archives": {}, "outs": {"a.out": {"objects": ["o/r.o", "o/t.o"]},
                                      "b.out": {"objects": ["o/t.o"]}}}
    (p.root / ".printsect" / "deps.json").write_text(json.dumps(graph), encoding="utf-8")
    capsys.readouterr()
    assert psect(["map", "--by-out", "--json", "--root", str(p.root)]) == 0
    names = {m["name"] for m in json.loads(capsys.readouterr().out)["modules"]["shown"]}
    assert names == {"a.out", "a.out+b.out"}


def tangled(p_files=8):
    files = {}
    for i in range(p_files):
        files[f"a/u{i}.c"] = entry((f"au{i}", False), refs=[f"bu{i}"])
        files[f"b/u{i}.c"] = entry((f"bu{i}", False), refs=[f"au{i}"])
    return Map(build(Index(files=files)))


def test_issues_finds_tangled_pairs():
    out = issues(tangled(), [], 10)
    assert out["mutual"]["shown"] == [{"a": "a/", "b": "b/", "a_uses_b": 8, "b_uses_a": 8}]
    assert not out["layers_set"]


def test_issues_finds_links_against_the_layers(project, capsys):
    p = project({**CHAIN, "drv/bad.c": b"int bad(void) { return mid(); }\n"},
                config="map:\n  layers: ['app/*', 'svc/*', 'drv/*']\n")
    _, out = data(capsys, "issues", "--root", str(p.root))
    assert out["against_layers"]["shown"][0]["user"] == "drv/bad.c"
    assert out["single_use_links"]["total"] >= 1
    capsys.readouterr()
    main(["issues", "--root", str(p.root)])
    assert "drv/bad.c -> svc/mid.c" in capsys.readouterr().out


def test_issues_says_layers_were_not_checked(project, capsys):
    p = project(CHAIN)
    capsys.readouterr()
    main(["issues", "--root", str(p.root)])
    assert "not checked" in capsys.readouterr().out


def test_a_snapshot_shows_what_changed(project, capsys):
    p = project(CHAIN)
    assert main(["save", "--root", str(p.root)]) == 0
    (p.root / "app" / "main.c").write_bytes(b"int main(void) { return other(); }\n")
    os.utime(p.root / "app" / "main.c", ns=(1, 1))
    (p.root / "app" / "extra.c").write_bytes(b"int x;\n")
    _, out = data(capsys, "diff", "--root", str(p.root))
    assert out["new_links"]["shown"] == [{"from": "app/", "to": "drv/", "count": 1}]
    assert out["gone_links"]["shown"] == [{"from": "app/", "to": "svc/", "count": 1}]
    assert out["files_added"]["shown"] == ["app/extra.c"]


def test_a_snapshot_finds_a_new_cycle(project, capsys):
    p = project(CHAIN)
    main(["save", "--root", str(p.root)])
    (p.root / "drv" / "low.c").write_bytes(b"int low(void) { return mid(); }\n")
    os.utime(p.root / "drv" / "low.c", ns=(1, 1))
    _, out = data(capsys, "diff", "--root", str(p.root))
    assert out["new_cycles"]["shown"] == [["drv/", "svc/"]]


@pytest.mark.parametrize("name", ["../x", "a/b", "a b", "C:x"])
def test_a_snapshot_name_cannot_leave_the_state_folder(project, capsys, name):
    p = project(CHAIN)
    assert main(["save", name, "--root", str(p.root)]) == 1


@pytest.mark.parametrize("saved", [
    "not json", "[]", '{"version": 99}',
    '{"version":1,"files":["a\\u001b.c"],"edges":[],"cycles":[],"modules":{},"unresolved":0}',
    '{"version":1,"files":[],"edges":[[1,2,3]],"cycles":[],"modules":{},"unresolved":0}',
])
def test_a_snapshot_that_is_not_ours_is_refused(project, capsys, saved):
    p = project(CHAIN)
    (p.root / ".printsect" / "map.last.snapshot.json").write_text(saved, encoding="utf-8")
    assert main(["diff", "--root", str(p.root)]) == 1
    assert "psect-map save" in capsys.readouterr().err


def test_diff_without_a_snapshot_says_so(project, capsys):
    p = project(CHAIN)
    assert main(["diff", "--root", str(p.root)]) == 1
    assert "psect-map save" in capsys.readouterr().err


def test_summary_says_what_to_read_first_and_what_it_covers(project, capsys):
    p = project({**CHAIN, "vendor/v.c": b"int vv(void) { return 0; }\n",
                 "svc/use.c": b"int use(void) { return vv(); }\n"})
    _, out = data(capsys, "summary", "--near", "low", "--root", str(p.root))
    assert out["entrances"][0]["module"] == "app/"
    assert out["scope"]["files"] == 6 and "contact" in out["note"]
    assert out["classes"]["third_party"] == 1
    assert out["product_uses_of_others"] == {"third_party": 1}
    assert out["way_in"] == ["app/main.c", "svc/mid.c", "drv/low.c"]
    assert out["widest_reach"][0]["reach"] >= 1
    capsys.readouterr()
    main(["summary", "--root", str(p.root)])
    text = capsys.readouterr().out
    assert "entrances:" in text and "left out:" in text


def test_the_agent_gets_the_new_answers_without_approval(project):
    p = project(CHAIN)
    names = {"map_impact", "map_issues", "map_summary", "map_save", "map_diff"}
    assert names <= set(BY_NAME) and not names & RUNS_SETTINGS
    for tool, arguments in (("map_impact", {"name": "low"}), ("map_issues", {}),
                            ("map_summary", {}), ("map_save", {}), ("map_diff", {})):
        text, is_error = call(BY_NAME[tool], arguments, str(p.root))
        assert not is_error, text


def test_psect_gives_the_page_the_map_on_the_same_paths(project, capsys):
    p = project(NET)
    deps_json(p)
    page = p.root / ".printsect" / "deps.html"
    assert psect(["deps", "html", "--no-open", "--root", str(p.root)]) == 0
    text = page.read_text(encoding="utf-8")
    assert '"map":{"modules":{' in text and '["net/tx.c","util/ring.c",["ring_put"]]' in text


def test_a_public_copy_masks_the_map_too(project, capsys):
    p = project(NET, config="public:\n  mask: [ring]\n")
    deps_json(p)
    assert psect(["deps", "html", "--public", "--no-open", "--root", str(p.root)]) == 0
    text = (p.root / ".printsect" / "deps.public.html").read_text(encoding="utf-8")
    data_part = text[text.index('id="data">'):text.index("</script>", text.index('id="data">'))]
    assert "ring" not in data_part and '"map":{"modules":{' in data_part


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


def test_impact_starts_from_a_whole_module(project, capsys):
    p = project(NET)
    _, out = data(capsys, "impact", "util/", "--root", str(p.root))
    assert out["module"] == "util/" and {r["file"] for r in out["files"]["shown"]} == {
        "net/rx.c", "net/tx.c", "app/main.c"}


def test_impact_names_what_it_could_not_find(project, capsys):
    p = project(NET)
    code, out = data(capsys, "impact", "nothing_like_this", "--root", str(p.root))
    assert code == 1 and "no module" in out["error"]


def test_path_gives_the_steps_and_their_symbols(project, capsys):
    p = project(NET)
    _, out = data(capsys, "path", "main", "ring_put", "--root", str(p.root))
    assert out["found"] and out["steps"][0]["from"] == "app/main.c"
    assert out["steps"][-1]["to"] == "util/ring.c" and out["steps"][-1]["via"] == ["ring_put"]
    assert len(out["steps"]) == 2


def test_path_between_modules(project, capsys):
    p = project(NET)
    _, out = data(capsys, "path", "app/", "util/", "--root", str(p.root))
    assert out["found"] and out["steps"][0]["from_module"] == "app/"


def test_path_says_when_only_the_other_way_exists(project, capsys):
    p = project(NET)
    _, out = data(capsys, "path", "ring_put", "main", "--root", str(p.root))
    assert not out["found"] and out["steps"] == [] and out["other_way_steps"] == 2


def test_unresolved_lists_each_name_with_where_and_why(project, capsys):
    p = project({"a.c": b"int f(void) { return 0; }\n", "b.c": b"int f(void) { return 1; }\n",
                 "c.c": b"int g(void) { return f() + h(); }\n",
                 "d.c": b"static int h(void) { return 0; }\n"})
    _, out = data(capsys, "unresolved", "--root", str(p.root))
    same = out["same_name"]["shown"][0]
    assert same["name"] == "f" and same["users"] == 1 and same["used_by"] == ["c.c"]
    assert [d["file"] for d in same["defined"]] == ["a.c", "b.c"]
    assert same["defined"][0]["line"] == 1
    assert out["static_only"]["shown"][0]["name"] == "h" and "static" in out["static_only_why"]


def test_unresolved_names_the_files_it_could_not_read_for_certain(project, capsys):
    p = project({"a.c": b"#ifdef X\nint f(void) { return 0; }\n#endif\n",
                 "b.c": b"int g(void) { return 0; }\n"})
    _, out = data(capsys, "unresolved", "--root", str(p.root))
    assert [r["file"] for r in out["files"]["shown"]] == ["a.c"]
    assert out["files"]["shown"][0]["inside_if"] == 1


def page_of(p):
    page = (p.root / ".printsect" / "map.html").read_text(encoding="utf-8")
    start = page.index('id="data">') + len('id="data">')
    return page, json.loads(page[start:page.index("</script>", start)])


def test_the_page_holds_the_map_and_fetches_nothing(project, capsys):
    p = project(NET)
    assert psect(["map", "html", "--no-open", "--root", str(p.root)]) == 0
    page, d = page_of(p)
    assert "https://" not in page
    assert "http://" not in page.replace("http://www.w3.org/2000/svg", "")
    assert "fetch(" not in page and "__DATA__" not in page and d["root"] == ""
    assert p.root.as_posix() not in page and str(p.root) not in page
    assert d["default"] == "directory" and set(d["groupings"]) == {"directory"}
    assert "util/ring.c" in d["files"] and "ring_put" in d["symbols"]


def test_the_page_says_the_line_of_each_use(project):
    p = project(NET)
    assert main(["html", "--no-open", "--root", str(p.root)]) == 0
    _, d = page_of(p)
    user, definer = d["files"].index("net/tx.c"), d["files"].index("util/ring.c")
    flat = next(e[2] for e in d["edges"] if e[:2] == [user, definer])
    assert d["symbols"][flat[0]] == "ring_put" and flat[1] == 1


def test_the_page_agrees_with_the_commands(project, capsys):
    p = project(NET)
    _, summary_answer = data(capsys, "summary", "--root", str(p.root))
    assert main(["html", "--no-open", "--root", str(p.root)]) == 0
    _, d = page_of(p)
    summary_page = d["views"]["directory"]["summary"]
    assert summary_page["central"] == summary_answer["central"]
    assert summary_page["classes"] == summary_answer["classes"]


def test_links_put_the_folder_on_the_page_only_when_asked(project):
    p = project(NET)
    assert main(["html", "--no-open", "--links", "--root", str(p.root)]) == 0
    assert page_of(p)[1]["root"] == p.root.as_posix()


def test_a_name_cannot_close_the_script_of_the_page():
    from printsect.map.page import render

    page = render({"files": ["a</script><img src=x>.c"]}, "</title><b>")
    assert "<img src=x>" not in page and "</title><b>" not in page


def test_the_page_lists_a_change_since_a_snapshot(project):
    p = project(NET)
    assert main(["save", "--root", str(p.root)]) == 0
    (p.root / "app" / "extra.c").write_bytes(b"int extra(void) { return ring_put(1); }\n")
    assert main(["html", "--no-open", "--root", str(p.root)]) == 0
    assert page_of(p)[1]["changes"]["last"]["files_added"]["shown"] == ["app/extra.c"]


def test_the_page_can_group_by_the_build(project):
    from printsect.deps import save
    from printsect.deps.graph import Graph

    p = project(NET)
    objects = {"o/rx.o": {"source": "net/rx.c"}, "o/tx.o": {"source": "net/tx.c"},
               "o/ring.o": {"source": "util/ring.c"}, "o/main.o": {"source": "app/main.c"}}
    save(p, Graph(objects=objects,
                  archives={"libnet.a": {"objects": ["o/rx.o", "o/tx.o", "o/ring.o"]}},
                  outs={"app.out": {"objects": ["o/main.o"], "archives": ["libnet.a"]}}))
    assert psect(["map", "html", "--no-open", "--root", str(p.root)]) == 0
    _, d = page_of(p)
    assert d["default"] == "archive" and set(d["groupings"]) == {"archive", "output", "directory"}
    assert "libnet.a" in d["groupings"]["archive"]["names"]


ACME = {"acme/rx.c": b"int acme_rx(int n) { return ring_put(n); }\n",
        "util/ring.c": b"int ring_put(int n) { return n; }\n"}
MASK = "public:\n  mask: [acme]\n"


def public_of(p):
    page = (p.root / ".printsect" / "map.public.html").read_text(encoding="utf-8")
    start = page.index('id="data">') + len('id="data">')
    return page, json.loads(page[start:page.index("</script>", start)])


def test_a_public_page_has_the_names_out_of_everything_it_works_out(project, capsys):
    p = project(ACME, config=MASK)
    (p.root / ".printsect").mkdir(exist_ok=True)
    assert main(["save", "--root", str(p.root)]) == 0
    capsys.readouterr()
    assert main(["html", "--public", "--no-open", "--root", str(p.root)]) == 0
    page, d = public_of(p)
    assert "acme" not in page.lower() and p.root.name not in page
    assert d["name"] == "project" and d["changes"] == {} and d["root"] == ""
    assert any(f.endswith("/rx.c") for f in d["files"]) and "util/ring.c" in d["files"]
    assert any(n.endswith("_rx") for n in d["symbols"]) and "ring_put" in d["symbols"]
    assert "masked: " in capsys.readouterr().out


def test_hiding_sources_replaces_files_symbols_and_folders(project):
    p = project(ACME, config=MASK + "  hide_sources: true\n")
    assert main(["html", "--public", "--no-open", "--root", str(p.root)]) == 0
    page, d = public_of(p)
    for name in ("ring_put", "util/", "rx.c", "acme"):
        assert name not in page
    assert all(f.startswith("file-") for f in d["files"]) and len(d["edges"]) == 1
    assert all(m.startswith("module-") for m in d["groupings"]["directory"]["names"])


def test_the_layers_still_count_once_the_names_are_gone(project):
    p = project({"low/a.c": b"int a(void) { return b(); }\n",
                 "high/b.c": b"int b(void) { return 0; }\n"},
                config="map:\n  layers: ['high/*', 'low/*']\npublic:\n  hide_sources: true\n")
    assert main(["html", "--public", "--no-open", "--root", str(p.root)]) == 0
    assert public_of(p)[1]["views"]["directory"]["issues"]["against_layers"]["total"] == 1


def test_a_public_page_with_links_is_refused(project, capsys):
    p = project(ACME)
    assert main(["html", "--public", "--links", "--no-open", "--root", str(p.root)]) == 1
    assert not (p.root / ".printsect" / "map.public.html").exists()


def test_a_public_page_is_not_written_past_a_refused_knowledge_file(project, capsys):
    p = project({**ACME, ".printsect/knowledge/n.yaml": b"mask: 3\n"})
    assert psect(["map", "html", "--public", "--no-open", "--root", str(p.root)]) == 1
    assert "refused" in capsys.readouterr().err
    assert not (p.root / ".printsect" / "map.public.html").exists()


def test_the_names_in_knowledge_files_are_masked_on_a_public_page(project):
    p = project({**ACME, ".printsect/knowledge/n.yaml": b"mask: [acme]\n"})
    assert psect(["map", "html", "--public", "--no-open", "--root", str(p.root)]) == 0
    assert "acme" not in public_of(p)[0].lower()


def test_an_output_with_a_plus_in_its_folder_is_masked_whole(project):
    from printsect.deps import save
    from printsect.deps.graph import Graph

    p = project(ACME, config="public:\n  hide_sources: false\n")
    save(p, Graph(objects={"r.o": {"source": "acme/rx.c"}, "u.o": {"source": "util/ring.c"}},
                  outs={"/data/a+b/secret/x.out": {"objects": ["r.o", "u.o"]},
                        "/data/two/y.out": {"objects": ["r.o"]}}))
    assert psect(["map", "html", "--public", "--no-open", "--root", str(p.root)]) == 0
    page, d = public_of(p)
    assert "/data" not in page and "secret" not in page
    assert "(outside)/x.out+(outside)/y.out" in d["groupings"]["output"]["names"]
