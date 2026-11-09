from __future__ import annotations

import io
import json
import os
import shutil
import subprocess

import pytest

from printsect.cli import main
from printsect.core.errors import PsectError
from printsect.deps import load, make_scanner, save
from printsect.deps import cli as cli_module
from printsect.deps import make as make_module
from printsect.deps.graph import Graph
from printsect.deps.makelog import split_commands
from printsect.deps.search import Matcher
from printsect.deps.tree import TreeOptions, build_tree, render_tree
from printsect.probe.ops import add
from printsect.suite.build import load_graph
from printsect.suite.doctor import _deps as doctor_deps

FILES = {
    "src/inc/com.h": b"#pragma once\n",
    "src/inc/net.h": b'#include "com.h"\n',
    "src/net/rx.c": b'#include <stdio.h>\n#include "net.h"\nint rx(void) { return 0; }\n',
    "src/net/tx.c": b"int tx(void) { return 0; }\n",
    "src/disk/blk.c": b'#include "com.h"\nint blk(void) { return 0; }\n',
    "src/com/util.c": b'#include "com.h"\nint util(void) { return 0; }\n',
}

# What `make -n -B -w` prints for a build of two outputs from three archives, with a
# sub-make, a `cd` in a recipe, a partial link and -l libraries.
LOG = """\
make: Entering directory '{root}'
xcc -c -Isrc/inc -DNAME=\\"rx\\" src/net/rx.c -o obj/rx.o
xcc -c -I src/inc \\
    src/net/tx.c -o obj/tx.o
rm -f lib/libnet.a obj/old.o
xar crus lib/libnet.a obj/rx.o obj/tx.o
make -C src/disk
make[1]: Entering directory '{root}/src/disk'
xcc -c -I../inc blk.c -o ../../obj/blk.o
xar rcs ../../lib/libdisk.a ../../obj/blk.o
make[1]: Leaving directory '{root}/src/disk'
cd src/com && xcc -c -I../inc util.c
xar rcs lib/libcom.a src/com/util.o
xld -r -o obj/netpart.o -Llib -lnet -lcom
xcc -c gen/ctors.c -o obj/ctors.o
xld -r -o build/netdrv.out obj/netpart.o obj/ctors.o
xld -o build/disk.out -Llib -ldisk lib/libcom.a -lm
make: Leaving directory '{root}'
"""


@pytest.fixture
def scanned(project):
    p = project(FILES)
    log = LOG.format(root=p.root.as_posix())
    graph = make_scanner(p.settings["deps"]).scan(p, log=log)
    save(p, graph)
    return p, graph


def test_split_commands_keeps_windows_paths_and_quotes():
    assert split_commands(r'cd src\net && cc -DX=\"a b\" "my dir\x.c"; echo hi') == [
        ["cd", r"src\net"],
        ["cc", '-DX="a', 'b"', r"my dir\x.c"],
        ["echo", "hi"],
    ]


def test_reads_compile_archive_and_link(scanned):
    _, g = scanned
    assert g.objects["obj/rx.o"]["source"] == "src/net/rx.c"
    assert g.objects["obj/blk.o"]["source"] == "src/disk/blk.c"  # from the sub-make
    assert g.objects["src/com/util.o"]["source"] == "src/com/util.c"  # from the cd
    assert g.archives["lib/libnet.a"]["objects"] == ["obj/rx.o", "obj/tx.o"]
    assert g.archives["lib/libdisk.a"]["objects"] == ["obj/blk.o"]
    assert g.outs["obj/netpart.o"]["archives"] == ["lib/libnet.a", "lib/libcom.a"]
    assert g.outs["build/netdrv.out"]["objects"] == ["obj/netpart.o", "obj/ctors.o"]
    assert g.outs["build/disk.out"]["archives"] == ["lib/libcom.a", "lib/libdisk.a"]
    assert g.outs["build/disk.out"]["libs"] == ["m"]
    assert g.roots() == ["build/disk.out", "build/netdrv.out"]
    assert "obj/old.o" not in g.objects  # rm is not a build step


def test_follows_includes_through_the_include_path(scanned):
    _, g = scanned
    assert g.objects["obj/rx.o"]["headers"] == ["src/inc/com.h", "src/inc/net.h"]
    assert g.objects["obj/blk.o"]["headers"] == ["src/inc/com.h"]
    assert g.objects["obj/tx.o"]["headers"] == []


def test_which_goes_up_to_every_output(scanned):
    _, g = scanned
    assert {r["root"] for r in g.which("src/net/rx.c")} == {"build/netdrv.out"}
    assert [r["chain"] for r in g.which("src/net/rx.c")] == [
        ["obj/rx.o", "lib/libnet.a", "obj/netpart.o", "build/netdrv.out"]
    ]
    assert {r["root"] for r in g.which("src/inc/com.h")} == {
        "build/netdrv.out", "build/disk.out"
    }
    assert g.which("src/nowhere.c") == []


def test_saved_and_loaded_with_a_page_for_people(scanned):
    p, g = scanned
    again = load(p)
    assert again.outs == g.outs and again.objects == g.objects
    md = (p.state_dir / "DEPS.md").read_text(encoding="utf-8")
    assert "src/net/rx.c" in md and "| src/inc/com.h |" in md
    assert "Makefile" not in json.dumps(again.inputs)  # no Makefile in this project
    (p.root / "Makefile").write_text("all:\n", encoding="utf-8")
    assert again.stale_inputs(p.root) == []  # a new Makefile is not watched yet


def lines(g, probes=None, **kw):
    return render_tree(build_tree(g, probes or {}, TreeOptions(**kw)), ascii=True)


def test_tree_shows_objects_and_sources(scanned):
    _, g = scanned
    out = lines(g, only=["netdrv"])
    assert out[0] == "build/netdrv.out"
    assert "|   |   |-- rx.o <- src/net/rx.c" in out
    assert not any("disk" in line for line in out)
    assert "|   |   |-- src/net/rx.c" in lines(g, only=["netdrv"], sources=True)


def test_tree_search_keeps_the_path_to_a_match(scanned):
    _, g = scanned
    out = lines(g, find=Matcher("rx"))
    assert out == [
        "build/netdrv.out",
        "`-- obj/netpart.o",
        "    `-- lib/libnet.a",
        "        `-- rx.o <- src/net/rx.c",
    ]
    whole = lines(g, find=Matcher("libcom"))  # a matching archive keeps what is in it
    assert any("util.o <- src/com/util.c" in line for line in whole)
    headers = lines(g, find=Matcher("*.h"))  # headers show when they match
    assert any(line.endswith("src/inc/net.h") for line in headers)


def test_tree_marks_probes_and_can_keep_only_them(scanned):
    p, g = scanned
    add(p, "src/net/rx.c", 3)
    add(p, "src/inc/com.h", 1)
    probes = {"src/net/rx.c": [1], "src/inc/com.h": [2]}
    out = lines(g, probes, probed=True)
    assert any("rx.o <- src/net/rx.c" in line and line.endswith("* #1") for line in out)
    assert any("src/inc/com.h" in line and line.endswith("* #2") for line in out)
    assert not any("tx.o" in line for line in out)


def test_cli_deps_commands(scanned, capsys):
    p, _ = scanned
    root = str(p.root)
    log = p.root / "make.log"
    log.write_text(LOG.format(root=p.root.as_posix()), encoding="utf-8")
    assert main(["deps", "--from-log", str(log), "--root", root]) == 0
    assert "2 outputs" in capsys.readouterr().out
    assert main(["deps", "tree", "--ascii", "-f", "blk", "--root", root]) == 0
    assert "blk.o <- src/disk/blk.c" in capsys.readouterr().out
    assert main(["deps", "which", "src/inc/net.h", "--json", "--root", root]) == 0
    assert json.loads(capsys.readouterr().out)["roots"] == ["build/netdrv.out"]
    assert main(["deps", "find", "com.h", "--quickfix", "--root", root]) == 0
    qf = capsys.readouterr().out.splitlines()
    assert any(line.endswith("com.h:1: disk.out > libcom.a > util.o") for line in qf)
    assert main(["deps", "which", "src/none.c", "--root", root]) == 1


def test_a_project_command_runs_when_asked(project, capsys):
    import sys

    p = project(FILES)
    (p.root / "make.log").write_text(LOG.format(root=p.root.as_posix()), encoding="utf-8")
    p = project(config=(
        "deps:\n  command:\n"
        f"    - {json.dumps(sys.executable)}\n"
        "    - -c\n"
        "    - \"print(open('make.log', encoding='utf-8').read())\"\n"
    ))
    assert main(["deps", "tree", "--ascii", "--root", str(p.root)]) == 1  # not run unasked
    assert main(["deps", "--root", str(p.root)]) == 0  # asked for: runs
    capsys.readouterr()
    assert main(["deps", "tree", "--ascii", "--root", str(p.root)]) == 0
    assert "rx.o <- src/net/rx.c" in capsys.readouterr().out
    assert (p.state_dir / "deps.json").is_file()


def test_queries_do_not_run_a_command_the_project_sets(project, capsys):
    marker = "ran.txt"
    p = project(FILES, config=(
        "deps:\n  command: [python, -c, \"open('ran.txt', 'w').write('x')\"]\n"))
    assert main(["deps", "tree", "--root", str(p.root)]) == 1
    assert "run psect deps first" in capsys.readouterr().err
    assert not (p.root / marker).exists()  # nothing ran


@pytest.mark.parametrize("question", [["tree"], ["which", "src/net/rx.c"], ["find", "rx"],
                                      ["html", "--no-open"]])
def test_a_question_does_not_read_the_build_by_itself(project, capsys, monkeypatch, question):
    # make -n still runs $(shell ...) in a Makefile, so even the stock command stays asked for.
    def ran(*args, **kwargs):
        raise AssertionError("a command ran")

    monkeypatch.setattr(make_module.subprocess, "run", ran)
    p = project(FILES)
    assert main(["deps", *question, "--root", str(p.root)]) == 1
    assert "run psect deps first" in capsys.readouterr().err
    assert not (p.root / ".printsect" / "deps.json").exists()


GRAPH = {
    "version": 1,
    "objects": {"o/rx.o": {"source": "src/net/rx.c", "headers": ["src/inc/net.h"]},
                "o/crt.o": {"source": None}},
    "archives": {"lib/net.a": {"objects": ["o/rx.o"], "made_in": "src/net"},
                 "lib/vendor.a": {"objects": [], "prebuilt": True}},
    "outs": {"out/app.out": {"objects": ["o/crt.o"], "archives": ["lib/net.a", "lib/vendor.a"],
                             "libs": ["m"]}},
}


def from_graph(p, tmp_path, graph):
    path = tmp_path / "build.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    return main(["deps", "--from-graph", str(path), "--root", str(p.root)])


def test_a_graph_file_is_read_without_running_anything(project, capsys, tmp_path, monkeypatch):
    def ran(*args, **kwargs):
        raise AssertionError("a command ran")

    monkeypatch.setattr(make_module.subprocess, "run", ran)
    p = project(FILES)
    assert from_graph(p, tmp_path, GRAPH) == 0
    g = load(p)
    assert g.final_outs() == ["out/app.out"]
    assert g.command == "(a graph file)" and g.headers_traced
    assert g.archives["lib/net.a"]["made_in"] == "src/net"
    assert {r["root"] for r in g.which("src/inc/net.h")} == {"out/app.out"}
    assert main(["deps", "tree", "--root", str(p.root)]) == 0


def test_a_graph_file_gives_the_map_its_archives(project, capsys, tmp_path):
    p = project(FILES)
    assert from_graph(p, tmp_path, GRAPH) == 0
    capsys.readouterr()
    assert main(["map", "--json", "--root", str(p.root)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["grouped_by"] == "archive" and out["files"] == 1


def test_a_graph_file_comes_from_stdin_and_may_start_with_a_bom(project, monkeypatch):
    p = project(FILES)
    raw = b"\xef\xbb\xbf" + json.dumps(GRAPH).encode("utf-8")
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(raw)))
    assert main(["deps", "--from-graph", "-", "--root", str(p.root)]) == 0
    assert load(p).final_outs() == ["out/app.out"]


def test_a_graph_file_without_headers_says_they_are_unknown(project, tmp_path):
    p = project(FILES)
    assert from_graph(p, tmp_path, {"objects": {"o/a.o": {"source": "a.c"}}}) == 0
    assert not load(p).headers_traced


@pytest.mark.parametrize(("graph", "said"), [
    ([], "expected an object"),
    ({"objects": {}, "extra": 1}, "unknown field 'extra'"),
    ({"version": 2}, "version 2"),
    ({"objects": []}, "expected an object keyed by path"),
    ({"objects": {"/abs/a.o": {}}}, "not a path inside"),
    ({"objects": {"C:/a.o": {}}}, "not a path inside"),
    ({"objects": {"//server/share/a.o": {}}}, "not a path inside"),
    ({"objects": {"../a.o": {}}}, "not a path inside"),
    ({"objects": {"a/../../b.o": {}}}, "not a path inside"),
    ({"objects": {"a\x1b[2J.o": {}}}, "not a path inside"),
    ({"objects": {"a.o": {"source": 3}}}, "not a path inside"),
    ({"objects": {"a.o": {"flags": []}}}, "unknown field 'flags'"),
    ({"objects": {"a.o": {"headers": "x.h"}}}, "expected a list"),
    ({"archives": {"l.a": {"objects": ["no.o"]}}}, "which is not listed"),
    ({"archives": {"l.a": {"objects": [], "prebuilt": "yes"}}}, "true or false"),
    ({"outs": {"a.out": {"archives": ["a.out"]}}}, "built from itself"),
    ({"outs": {"a.out": {"objects": ["b.out"]}, "b.out": {"objects": ["a.out"]}}},
     "built from itself"),
    ({"inputs": ["missing.csv"]}, "cannot read missing.csv"),
])
def test_a_graph_file_that_is_wrong_stops_and_says_where(project, capsys, tmp_path, graph, said):
    p = project(FILES)
    assert from_graph(p, tmp_path, graph) == 1
    assert said in capsys.readouterr().err
    assert not (p.root / ".printsect" / "deps.json").exists()


def test_a_graph_file_that_is_not_json_or_not_there(project, capsys, tmp_path):
    p = project(FILES)
    (tmp_path / "bad.json").write_bytes(b"{")
    assert main(["deps", "--from-graph", str(tmp_path / "bad.json"), "--root", str(p.root)]) == 1
    assert "not JSON" in capsys.readouterr().err
    assert main(["deps", "--from-graph", str(tmp_path / "no.json"), "--root", str(p.root)]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_from_log_and_from_graph_cannot_be_given_together():
    with pytest.raises(SystemExit):
        main(["deps", "--from-log", "a", "--from-graph", "b"])


def test_a_changed_input_of_a_graph_file_stops_the_build_instead_of_running_make(
        project, tmp_path, monkeypatch):
    def ran(*args, **kwargs):
        raise AssertionError("a command ran")

    monkeypatch.setattr(make_module.subprocess, "run", ran)
    p = project({**FILES, "targets.csv": b"a\n"})
    assert from_graph(p, tmp_path, {**GRAPH, "inputs": ["targets.csv"]}) == 0
    assert load_graph(p, lambda text: None).command == "(a graph file)"
    os.utime(p.root / "targets.csv", ns=(1, 1))
    with pytest.raises(PsectError, match="--from-graph"):
        load_graph(p, lambda text: None)


def test_impact_goes_from_a_header_to_every_output(scanned, capsys):
    p, _ = scanned
    capsys.readouterr()
    assert main(["deps", "impact", "src/inc/com.h", "--json", "--root", str(p.root)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "lib/libnet.a" in out["archives"] or "lib/libdisk.a" in out["archives"]
    assert set(out["outs"]) == {"build/netdrv.out", "build/disk.out"}
    assert out["status"] == "inferred"


@pytest.mark.parametrize("name", [r"\\server\share\a.c", "//server/share/a.c", r"\\?\C:\a.c"])
@pytest.mark.parametrize("action", ["impact", "which"])
def test_a_network_path_is_only_looked_up_never_opened(scanned, capsys, monkeypatch, name, action):
    p, _ = scanned
    root = str(p.root)

    def opened(path):
        raise AssertionError(f"{path} was touched")

    monkeypatch.setattr(cli_module, "Path", opened)  # the only way this command opens a path
    assert main(["deps", action, name, "--root", root]) == 1
    capsys.readouterr()


def test_impact_depth_stops_early(scanned, capsys):
    p, g = scanned
    near = g.impact("src/net/rx.c", depth=1)
    assert near["objects"] == ["obj/rx.o"] and near["archives"] == [] and near["outs"] == []
    far = g.impact("src/net/rx.c")
    assert far["outs"] == ["build/netdrv.out"]
    assert main(["deps", "impact", "nothing.c", "--root", str(p.root)]) == 1


def test_a_graph_file_says_what_is_made_deployed_left_out_and_how_sure(project, capsys, tmp_path):
    p = project(FILES)
    graph = {**GRAPH, "objects": {**GRAPH["objects"], "o/gen.o": {"source": "gen/a.c",
                                                                  "status": "confirmed"}},
             "archives": {**GRAPH["archives"], "lib/gen.a": {"objects": ["o/gen.o"]}},
             "outs": {"out/app.out": {"archives": ["lib/net.a", "lib/gen.a"]}},
             "made": {"gen/a.c": {"from": ["a.def"], "by": "tools/gen.py"}},
             "copies": {"target:/lib/app.out": "out/app.out"},
             "excluded": {"vendor/x.c": "not ours"}}
    assert from_graph(p, tmp_path, graph) == 0
    g = load(p)
    assert g.status_of("o/gen.o") == "confirmed" and g.status_of("lib/net.a") == "script"
    assert g.status_of("lib/vendor.a") == "unresolved" and g.status_of("o/crt.o") == "unresolved"
    assert g.status_of("vendor/x.c") == "excluded"
    capsys.readouterr()
    assert main(["deps", "impact", "a.def", "--json", "--root", str(p.root)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["made"] == ["gen/a.c"] and out["objects"] == ["o/gen.o"]
    assert out["outs"] == ["out/app.out"] and out["deployed"] == ["target:/lib/app.out"]


@pytest.mark.parametrize(("extra", "said"), [
    ({"objects": {"a.o": {"status": "sure"}}}, ".status: one of"),
    ({"copies": {"t\x1b": "a.o"}}, "plain text"),
    ({"copies": {"t": "no.o"}}, "which is not listed"),
    ({"made": {"g.c": {"from": "a"}}}, "expected a list"),
])
def test_the_extra_parts_of_a_graph_file_are_checked(project, capsys, tmp_path, extra, said):
    p = project(FILES)
    assert from_graph(p, tmp_path, extra) == 1
    assert said in capsys.readouterr().err


def test_a_scanned_build_says_where_each_part_came_from(scanned):
    _, g = scanned
    assert g.status_of("obj/rx.o") == "build" and g.status_of("src/inc/net.h") == "inferred"


def test_doctor_keeps_a_graph_file_and_says_when_it_is_stale(project, tmp_path, monkeypatch):
    def ran(*args, **kwargs):
        raise AssertionError("a command ran")

    monkeypatch.setattr(make_module.subprocess, "run", ran)
    p = project({**FILES, "targets.csv": b"a\n"})
    assert from_graph(p, tmp_path, {**GRAPH, "inputs": ["targets.csv"]}) == 0
    finding, graph = doctor_deps(p)
    assert finding.status == "ok" and graph.command == "(a graph file)"
    os.utime(p.root / "targets.csv", ns=(1, 1))
    finding, _ = doctor_deps(p)
    assert finding.status == "warn" and "targets.csv" in finding.message
    assert load(p).command == "(a graph file)"


def test_failing_build_command_is_reported(project, capsys):
    p = project({}, config="deps:\n  command: [python, -c, 'raise SystemExit(2)']\n")
    assert main(["deps", "--root", str(p.root)]) == 1
    assert "exit 2" in capsys.readouterr().err


def test_empty_graph_round_trip():
    assert Graph.from_dict(Graph().to_dict()) == Graph()


MAKEFILE = """\
all: build/app.out
build/app.out: net
\txld -o $@ -Llib -lnet
.PHONY: net
net:
\t$(MAKE) -C src/net
"""
SUB_MAKEFILE = """\
../../lib/libnet.a: rx.o
\txar rcs $@ $^
rx.o: rx.c
\txcc -c -I../inc $< -o $@
"""


@pytest.mark.skipif(shutil.which("make") is None, reason="needs GNU make")
def test_real_make_dry_run(project):
    try:
        subprocess.run(["make", "--version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("make does not run here")
    p = project({**FILES, "Makefile": MAKEFILE.encode(),
                 "src/net/Makefile": SUB_MAKEFILE.encode()})
    graph = make_scanner(p.settings["deps"]).scan(p)  # the tools do not exist: -n runs none
    assert graph.objects["src/net/rx.o"]["source"] == "src/net/rx.c"
    assert graph.objects["src/net/rx.o"]["headers"] == ["src/inc/com.h", "src/inc/net.h"]
    assert graph.archives["lib/libnet.a"]["objects"] == ["src/net/rx.o"]
    assert graph.outs["build/app.out"]["archives"] == ["lib/libnet.a"]
    assert set(graph.inputs) == {"Makefile", "src/net/Makefile"}


@pytest.mark.skipif(shutil.which("make") is None, reason="needs GNU make")
def test_example_demo_project(tmp_path):
    from pathlib import Path

    from printsect.core.config import load_project

    src = Path(__file__).parents[2] / "examples" / "demo"
    shutil.copytree(src, tmp_path / "demo", ignore=shutil.ignore_patterns(".printsect", "build"))
    p = load_project(tmp_path / "demo")
    g = make_scanner(p.settings["deps"]).scan(p)
    assert g.final_outs() == ["build/out/diskdrv.out", "build/out/netdrv.out"]
    assert {r["root"] for r in g.which("util/ring.h")} == set(g.final_outs())
    assert {r["root"] for r in g.which("net/net.h")} == {"build/out/netdrv.out"}
    assert g.archives["build/lib/libdisk.a"]["objects"] == [
        "build/obj/disk/blk.o", "build/obj/disk/cache.o"
    ]


def test_html_page_is_self_contained_and_safe(scanned):
    import re

    from printsect.deps.html import render_html

    p, g = scanned
    g.objects["obj/x.o"] = {"source": "src/</script><b>x.c", "headers": []}
    page = render_html(g, {"src/net/rx.c": [{"n": 1, "group": "g", "purpose": "a<b"}]},
                       name="demo")
    block = re.search(r'<script type="application/json" id="data">(.*?)</script>', page, re.S)
    data = json.loads(block.group(1))
    paths = data["paths"]
    obj = next(o for o in data["objects"] if paths[o[0]] == "obj/x.o")
    assert paths[obj[1]] == "src/</script><b>x.c"
    assert len(paths) == len(set(paths))  # each path written once
    assert data["probes"]["src/net/rx.c"][0]["purpose"] == "a<b"
    assert "</script><b>" not in block.group(1)  # the data cannot end the block
    assert not re.search(r"(src|href)=[\"']?https?:", page)  # nothing is fetched
    assert "@import" not in page and "fetch(" not in page
    assert str(p.root) not in page and p.root.as_posix() not in page


def page_script(page):
    """The page's own script, with what it was made from filled in."""
    return page[page.rindex("<script>") + len("<script>"):page.rindex("</script>")]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_script_of_the_page_parses(scanned, tmp_path):
    from printsect.deps.html import render_html

    _, g = scanned
    script = tmp_path / "page.js"
    script.write_text(page_script(render_html(g, {}, name="p")), encoding="utf-8")
    done = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_page_says_how_sure_each_part_is_and_has_every_view(scanned):
    from printsect.deps.html import render_html

    _, g = scanned
    g.objects["obj/rx.o"]["status"] = "unresolved"
    page = render_html(g, {}, name="p")
    assert '"status":[[' in page and '"default_status":"build"' in page
    for name in ("tab-graph", "tab-status", "tab-modules", "g-layout", "xpng", "mini"):
        assert name in page
    assert "__GRAPH_JS__" not in page


def test_cli_deps_html_writes_the_page(scanned, capsys):
    p, _ = scanned
    out = p.root / "page.html"
    assert main(["deps", "html", "--no-open", "-o", str(out), "--root", str(p.root)]) == 0
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")
    capsys.readouterr()
    assert main(["deps", "html", "--json", "--root", str(p.root)]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["opened"] is False and answer["file"].endswith("deps.html")
    assert (p.state_dir / "deps.html").is_file()


def test_large_graph_queries_stay_bounded():
    # One header read by 2000 objects in 40 archives, linked into 10 outputs each.
    g = Graph()
    for a in range(40):
        objs = [f"obj/{a}/{i}.o" for i in range(50)]
        for o in objs:
            g.objects[o] = {"source": o.replace(".o", ".c"), "headers": ["inc/all.h"]}
        g.archives[f"lib/l{a}.a"] = {"objects": objs}
    for n in range(10):
        g.outs[f"out/{n}.out"] = {"objects": [], "archives": list(g.archives), "libs": []}
    assert len(g.which("inc/all.h")) == 500  # capped
    assert g.roots_for_file("inc/all.h") == set(g.outs)  # complete all the same
    assert len(g._cache["roots_of"]) >= 2000  # remembered per object


def test_cli_which_summarises_long_answers(project, capsys):
    p = project({})
    g = Graph()
    for i in range(30):
        g.objects[f"o{i}.o"] = {"source": f"s{i}.c", "headers": ["x.h"]}
    g.archives["l.a"] = {"objects": list(g.objects)}
    save(p, g)
    assert main(["deps", "which", "x.h", "--ascii", "--root", str(p.root)]) == 0
    out = capsys.readouterr().out
    assert "... 10 more ways" in out and out.rstrip().endswith("built into: l.a")
    assert main(["deps", "which", "x.h", "--all", "--root", str(p.root)]) == 0
    assert "more ways" not in capsys.readouterr().out


def test_headers_can_be_turned_off(project, capsys):
    p = project(FILES, config="deps:\n  headers: false\n")
    g = make_scanner(p.settings["deps"]).scan(p, log=LOG.format(root=p.root.as_posix()))
    save(p, g)
    assert g.headers_traced is False
    assert g.objects["obj/rx.o"]["headers"] == []
    assert g.which("src/net/rx.c")  # sources still lead to their outputs
    assert load(p).headers_traced is False
    assert "deps.headers is false" in (p.state_dir / "DEPS.md").read_text(encoding="utf-8")
    root = str(p.root)
    assert main(["deps", "which", "src/inc/net.h", "--root", root]) == 1
    assert "deps.headers: false" in capsys.readouterr().out
    assert main(["probe", "add", "src/inc/com.h", "1", "--root", root]) == 0
    assert "deps.headers: false" in capsys.readouterr().out
    assert main(["probe", "add", "src/net/tx.c", "1", "--root", root]) == 0
    assert "warning" not in capsys.readouterr().out


def test_psect_deps_runs_on_its_own(scanned, capsys):
    from printsect.deps.cli import main as deps_main

    p, _ = scanned
    add(p, "src/net/rx.c", 3)
    root = str(p.root)
    assert deps_main(["tree", "--ascii", "-f", "rx", "--root", root]) == 0
    alone = capsys.readouterr().out
    assert "rx.o <- src/net/rx.c" in alone and "#1" not in alone  # no probe marks alone
    assert main(["deps", "tree", "--ascii", "-f", "rx", "--root", root]) == 0
    assert "* #1" in capsys.readouterr().out  # psect joins the probe marks in
