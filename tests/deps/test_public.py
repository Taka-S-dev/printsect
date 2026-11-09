"""What a public copy still must not hold."""

from __future__ import annotations

import json

import pytest

from printsect.cli import main
from printsect.deps import save
from printsect.deps.graph import Graph
from printsect.deps.public import Masker, leaks, mask_graph, mask_map, project_text
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


def unapproved_project(project):
    p = project({"a.c": b"int a(void) { return 0; }\n"}, config="deps:\n  command: make x\n")
    save(p, Graph(objects={"a.o": {"source": "a.c"}}, archives={"l.a": {"objects": ["a.o"]}}))
    return p


@pytest.mark.parametrize(("tool", "arguments"), [("map", {}), ("map_summary", {}),
                                                 ("map_issues", {}), ("map_who", {"name": "a"})])
def test_every_map_tool_leaves_the_saved_build_alone_before_approval(project, tool, arguments):
    p = unapproved_project(project)
    text, is_error = call(BY_NAME[tool], arguments, str(p.root))
    assert not is_error, text
    assert "l.a" not in text


def test_a_person_still_gets_the_archives(project, capsys):
    p = unapproved_project(project)
    capsys.readouterr()
    assert main(["map", "--json", "--root", str(p.root)]) == 0
    assert json.loads(capsys.readouterr().out)["grouped_by"] == "archive"


def test_the_names_of_libraries_are_checked_too():
    text = project_text({"outs": [[0, [], [], ["acme"]]]})
    assert leaks(text, ["acme"], "") == ["names to mask: 1"]


def test_paths_and_secrets_are_looked_for_in_everything_on_the_page():
    found = leaks('{"command": "C:/work/x password=hunter2"}', ["acme"], "", own="")
    assert found == ["absolute paths: 1", "something like a password or key: 1"]


def test_a_public_page_is_not_written_past_a_refused_knowledge_file(project, capsys):
    p = project({"a.c": b"int a;\n", ".printsect/knowledge/names.yaml": b"mask: 3\n"})
    save(p, Graph(objects={"a.o": {"source": "a.c"}}))
    assert main(["deps", "html", "--public", "--no-open", "--root", str(p.root)]) == 1
    assert "refused" in capsys.readouterr().err
    assert not (p.root / ".printsect" / "deps.public.html").exists()


def test_the_build_groupings_of_the_map_are_masked_too():
    masker = Masker(["acme"], True)
    done = mask_map({"modules": {"a.c": "x/"}, "by_archive": {"a.c": "libacme.a"},
                     "by_output": {"a.c": "acme.out"}, "edges": []}, masker)
    assert "acme" not in json.dumps(done)
    assert done["by_archive"]["file-1.c"].startswith("module-")
    assert done["by_output"]["file-1.c"].startswith("module-")


def graph_file(p, extra):
    from printsect.deps.imported import read_graph

    text = json.dumps({"objects": {"a.o": {"source": "a.c", **extra}}})
    save(p, read_graph(text, p))


def data_of(p, name):
    page = (p.root / ".printsect" / name).read_text(encoding="utf-8")
    return page[page.index('id="data">'):page.index("</script>", page.index('id="data">'))]


def test_a_name_and_origin_reach_the_page_and_stay_out_of_a_public_copy(project):
    p = project({"a.c": b"int a;\n"})
    graph_file(p, {"name": "Ring driver", "origin": "targets.csv:3"})
    assert main(["deps", "html", "--no-open", "--root", str(p.root)]) == 0
    assert "Ring driver" in data_of(p, "deps.html") and "targets.csv:3" in data_of(p, "deps.html")
    assert main(["deps", "html", "--public", "--no-open", "--root", str(p.root)]) == 0
    assert "Ring driver" not in data_of(p, "deps.public.html")
    assert "targets.csv:3" not in data_of(p, "deps.public.html")


def test_a_label_that_could_reach_a_terminal_is_refused(project):
    from printsect.core.errors import PsectError

    p = project({"a.c": b"int a;\n"})
    with pytest.raises(PsectError, match="origin"):
        graph_file(p, {"origin": "a\x1b[2J"})


def test_the_page_can_group_the_map_by_what_the_build_makes(project):
    p = project({"a.c": b"int a(void) { return 0; }\n"})
    save(p, Graph(objects={"a.o": {"source": "a.c"}}, archives={"l.a": {"objects": ["a.o"]}},
                  outs={"x.out": {"archives": ["l.a"]}}))
    assert main(["deps", "html", "--no-open", "--root", str(p.root)]) == 0
    text = data_of(p, "deps.html")
    assert '"by_archive":{"a.c":"l.a"}' in text and '"by_output":{"a.c":"x.out"}' in text


def test_every_output_a_file_is_grouped_under_is_masked():
    done = mask_map({"modules": {}, "edges": [],
                     "by_output": {"a.c": "/data/one/a.out+/data/two/b.out"}}, Masker([], False))
    assert done["by_output"]["a.c"] == "(outside)/a.out+(outside)/b.out"
