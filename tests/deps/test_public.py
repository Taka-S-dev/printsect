"""What a public copy still must not hold."""

from __future__ import annotations

import json

import pytest

from printsect.cli import main
from printsect.deps import save
from printsect.deps.graph import Graph
from printsect.deps.public import Masker, leaks, mask_graph, project_text
from printsect.suite.mcp import BY_NAME, call


def test_a_suffix_comes_from_the_last_part_of_the_path_only():
    m = Masker([], True)
    assert m.file("acme.board/rx") == "file-1"
    assert m.file("src/a.c") == "file-2.c"
    assert m.file("a.very-long-suffix-of-acme") == "file-3"


def test_hiding_sources_also_hides_modules_include_folders_and_symbols():
    m = Masker([], True)
    g = mask_graph(Graph(objects={"o.o": {"source": "a.c", "include_dirs": ["acme/inc"]}}), m)
    assert g.objects["file-1.o"]["include_dirs"] == []
    assert m.module("acme/net/") == "module-1" and m.symbols(["acme_init"]) == []


def test_a_deploy_target_never_goes_out_as_it_is():
    m = Masker([], False)
    g = mask_graph(Graph(outs={"a.out": {}}, copies={"10.0.0.5:/opt/acme/a.out": "a.out"}), m)
    assert g.copies == {"target-1": "a.out"} and m.report()["targets"] == 1


def test_a_short_name_is_not_found_inside_the_pages_own_words():
    data = {"default_status": "build", "paths": ["src/a.c"], "name": "project"}
    assert leaks(project_text(data), ["ui"], "") == []
    data["paths"] = ["ui/a.c"]
    assert leaks(project_text(data), ["ui"], "") == ["names to mask: 1"]


def test_the_check_looks_at_the_map_and_the_probes():
    data = {"map": {"modules": {"acme/a.c": "x/"}, "edges": [["b.c", "c.c", ["acme_f"]]]},
            "probes": {"acme.c": [{"n": 1}]}}
    assert leaks(project_text(data), ["acme"], "") == ["names to mask: 3"]


@pytest.mark.parametrize("name", ["a\x1b[2J.o", "\x07.o"])
def test_build_names_with_control_characters_stay_out_of_the_map_answer(project, capsys, name):
    p = project({"a.c": b"int a(void) { return 0; }\n"})
    save(p, Graph(objects={name: {"source": "a.c"}}, archives={"l.a": {"objects": [name]}}))
    capsys.readouterr()
    assert main(["map", "impact", "a.c", "--json", "--root", str(p.root)]) == 0
    assert "build" not in json.loads(capsys.readouterr().out)


def test_an_agent_gets_no_build_answer_before_a_person_approved(project):
    p = project({"a.c": b"int a(void) { return 0; }\n"}, config="deps:\n  command: make x\n")
    save(p, Graph(objects={"a.o": {"source": "a.c"}}, archives={"l.a": {"objects": ["a.o"]}}))
    text, is_error = call(BY_NAME["map_impact"], {"name": "a.c"}, str(p.root))
    assert not is_error and "build" not in json.loads(text)


def test_a_person_at_the_terminal_gets_the_build_answer(project, capsys):
    p = project({"a.c": b"int a(void) { return 0; }\n"}, config="deps:\n  command: make x\n")
    save(p, Graph(objects={"a.o": {"source": "a.c"}}, archives={"l.a": {"objects": ["a.o"]}}))
    capsys.readouterr()
    assert main(["map", "impact", "a.c", "--json", "--root", str(p.root)]) == 0
    assert json.loads(capsys.readouterr().out)["build"]["archives"] == ["l.a"]
