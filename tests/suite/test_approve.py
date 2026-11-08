"""An agent runs commands from the settings only once a person approved them."""

from __future__ import annotations

import json
import os
import sys

from printsect.cli import main
from printsect.core.config import load_project
from printsect.suite import approve as ap
from printsect.suite.approve import approve, commands, digest, unapproved
from printsect.suite.mcp import BY_NAME, TOOLS, call

MARK = "ran.txt"


def approve_now(p):
    """What a person does at a terminal: approve what they were shown."""
    return approve(p, digest(p))
ONE_STEP = "build: {steps: [{run: make}]}\n"


def sneaky(tmp_path) -> str:
    """A deps.command that leaves a file behind when it runs."""
    py = json.dumps(sys.executable)
    return f"deps:\n  command: [{py}, -c, \"open('{(tmp_path / MARK).as_posix()}', 'w')\"]\n"


def findings(text: str) -> dict[str, dict[str, str]]:
    return {f["name"]: f for f in json.loads(text.split("\n(exit")[0])["findings"]}


def test_the_commands_in_the_settings_are_listed(project):
    p = project({}, config=(
        "deps: {command: make -C src -n -B -w}\n"
        "build: {steps: [{run: make clean}, {for: outs, run: 'make {path}'}]}\n"
        "deploy: {type: command, commands: [mytool put x]}\n"
        "console: {type: command, read: [mytool, screen], send: [mytool, send, '{text}']}\n"
        "reproduce: [{send: app_start}, {sleep: 1}]\n"))
    listed = commands(p)
    assert listed[0] == "deps (make): make -C src -n -B -w (in .)"
    assert listed[1].startswith("project files make may read (all but C sources and build "
                                "outputs): 1 file(s), sha256 ")  # printsect.yaml
    assert listed[2:] == [
        "build.steps[0] (once): make clean",
        "build.steps[1] (outs): make {path}",
        "deploy.type: command",
        "deploy: mytool put x",
        "console.type: command",
        "console.read: mytool screen",
        "console.send: mytool send {text}",
        "reproduce[0] sends to the target: app_start",
    ]


def test_even_the_stock_make_n_needs_approving_for_an_agent(project):
    # make -n still runs $(shell ...) in Makefiles: reading the build is running code.
    listed = commands(project({}, config=""))
    assert listed[0] == "deps (make): make -n -B -w (in .)"
    assert listed[1].startswith("project files make may read")


def test_a_setting_in_an_odd_shape_is_listed_not_skipped(project):
    p = project({}, config="build: {steps: {run: make}}\ndeploy: [x]\n")
    listed = commands(p)
    assert any(x.startswith("build.steps: {") and "(not a list)" in x for x in listed)
    assert "deploy: ['x'] (not a mapping)" in listed
    q = project({}, config="build: {steps: [{run: make, shell: yes}]}\n")
    assert any(x.startswith("build.steps[0]: {") for x in commands(q))


def test_approval_holds_until_a_command_or_a_makefile_changes(project):
    p = project({"Makefile": b"all:\n"}, config=ONE_STEP)
    assert "build.steps[0] (once): make" in unapproved(p)
    approve_now(p)
    assert unapproved(load_project(p.root)) == []
    (p.root / "printsect.yaml").write_text("build: {steps: [{run: make all}]}\n",
                                           encoding="utf-8")
    assert "build.steps[0] (once): make all" in unapproved(load_project(p.root))
    approve_now(load_project(p.root))
    (p.root / "Makefile").write_bytes(b"all:\n\t$(shell echo hi)\n")  # a $(shell) slipped in
    assert unapproved(load_project(p.root))


def test_what_is_approved_is_what_runs(project):
    p = project({}, config="build: {steps: [{run: make all}]}\n")
    approve_now(p)
    for changed in ("build: {steps: [{run: [make, all]}]}\n",  # the same words, as a list
                    "build: {steps: [{run: make all}], cwd: elsewhere}\n",
                    "build: {steps: [{run: make all}]}\nreproduce: [{send: reboot}]\n"):
        (p.root / "printsect.yaml").write_text(changed, encoding="utf-8")
        assert unapproved(load_project(p.root)), changed


def test_settings_that_cannot_be_read_block_the_agent(project):
    p = project({})
    (p.root / "printsect.yaml").write_text("deps: [unclosed\n", encoding="utf-8")
    text, failed = call(BY_NAME["deps_scan"], {}, str(p.root))
    assert failed and "could not be checked" in text


def test_an_agent_cannot_run_an_unapproved_command(project, tmp_path):
    p = project({}, config=sneaky(tmp_path))
    root = str(p.root)
    for name in ("deps_scan", "build_plan", "deps_which", "run"):
        args = {"file": "a.c"} if name == "deps_which" else {}
        text, failed = call(BY_NAME[name], args, root)
        assert failed and "not been approved by a person" in text, name
    text, _ = call(BY_NAME["doctor"], {}, root)  # doctor still answers, naming it
    found = findings(text)
    assert found["deps"]["status"] == "warn" and "not approved" in found["deps"]["message"]
    assert found["approval"]["status"] == "warn"
    assert not (tmp_path / MARK).exists()  # nothing ran


def test_doctor_for_an_agent_reads_no_makefile_before_approval(project, tmp_path):
    # A Makefile whose $(shell) would leave a file behind, under the stock make -n.
    mark = (tmp_path / MARK).as_posix()
    p = project({"Makefile": f"X := $(shell echo > {mark})\nall:\n\t@true\n".encode()})
    text, _ = call(BY_NAME["doctor"], {}, str(p.root))
    assert findings(text)["deps"]["status"] == "warn"
    assert not (tmp_path / MARK).exists()


def test_once_approved_the_agent_may(project, tmp_path):
    p = project({}, config=sneaky(tmp_path))
    approve_now(p)
    call(BY_NAME["deps_scan"], {}, str(p.root))
    assert (tmp_path / MARK).exists()


def test_approve_is_for_people_only(project, capsys):
    assert "approve" not in {t.name for t in TOOLS}  # never offered to an agent
    p = project({}, config=ONE_STEP)
    assert main(["approve", "--show", "--root", str(p.root)]) == 0
    assert "NOT approved" in capsys.readouterr().out
    assert main(["approve", "--root", str(p.root)]) == 1  # no terminal: refused
    assert "needs a person at a terminal" in capsys.readouterr().err
    import pytest

    with pytest.raises(SystemExit):  # there is no --yes to approve without a person
        main(["approve", "--yes", "--root", str(p.root)])
    assert unapproved(load_project(p.root))


def test_a_record_in_the_project_does_not_approve(project):
    p = project({}, config=ONE_STEP)
    (p.state_dir / "approved.json").write_text('{"anything": {}}', encoding="utf-8")
    assert unapproved(p)  # approvals live outside the project


def test_a_forged_or_moved_record_does_not_approve(project, tmp_path):
    p = project({}, config=ONE_STEP)
    approve_now(p)
    store = ap._store() / "approvals.json"
    assert os.environ["PSECT_CONFIG_DIR"] in str(store)  # the test's own folder
    data = json.loads(store.read_text(encoding="utf-8"))
    record = data[str(p.root)]
    other = tmp_path / "other"
    (other / ".printsect").mkdir(parents=True)
    (other / "printsect.yaml").write_text(ONE_STEP, encoding="utf-8")
    data[str(other)] = record  # the same record, put under another project
    store.write_text(json.dumps(data), encoding="utf-8")
    assert unapproved(load_project(other))
    for d in record:
        record[d]["mac"] = "0" * 64  # a record written without the key
    store.write_text(json.dumps({str(p.root): record}), encoding="utf-8")
    assert unapproved(load_project(p.root))


def test_control_characters_are_shown_not_obeyed(project):
    cr, esc = chr(13), chr(27)
    p = project({}, config=f'build: {{steps: [{{run: "make{cr}rm -rf x{esc}[2K"}}]}}\n'
                .replace(cr, "\\r").replace(esc, "\\x1b"))
    shown = " ".join(commands(p))
    assert "\\x0d" in shown and "\\x1b" in shown
    assert cr not in shown and esc not in shown


def test_every_file_but_c_sources_and_outputs_is_covered(project):
    # However make would reach it (a variable, $(eval), an odd name), a changed file counts.
    p = project({"Makefile": b"include $(CFG)\n$(eval include x)\nall:\n",
                 "conf/whatever.cfg": b"A = 1\n", "rx.c": b"int rx;\n",
                 "build/rx.o": b"obj", "build/app.out": b"out"})
    approve_now(p)
    for name in ("rx.c", "build/rx.o", "build/app.out"):  # probes, and what the build writes
        (p.root / name).write_bytes(b"changed")
        assert not unapproved(load_project(p.root)), name
    (p.root / "conf" / "whatever.cfg").write_bytes(b"A = $(shell echo hi)\n")
    assert unapproved(load_project(p.root))
    approve_now(load_project(p.root))
    (p.root / "new.mk").write_bytes(b"B = 1\n")  # a new file, too
    assert unapproved(load_project(p.root))


def test_what_the_build_writes_can_be_left_out_once_approved(project):
    p = project({"Makefile": b"all:\n", "build/stamp.txt": b"1"},
                config="deps: {approval_ignore: ['build/*.txt']}\n")
    approve_now(p)
    (p.root / "build" / "stamp.txt").write_bytes(b"2")
    assert not unapproved(load_project(p.root))
    assert "deps.approval_ignore: ['build/*.txt']" in commands(p)  # the person sees it
    (p.root / "printsect.yaml").write_text("deps: {approval_ignore: ['**']}\n",
                                           encoding="utf-8")
    assert unapproved(load_project(p.root))  # widening it needs approving again


def test_nothing_is_approved_if_it_changed_while_asking(project):
    import pytest

    from printsect.core.errors import PsectError

    p = project({"Makefile": b"all:\n"})
    shown = digest(p)
    (p.root / "Makefile").write_bytes(b"all:\n\t$(shell echo hi)\n")  # changed meanwhile
    with pytest.raises(PsectError, match="changed while you were asked"):
        approve(load_project(p.root), expected=shown)
    assert unapproved(load_project(p.root))


def test_a_device_name_must_be_a_plain_word(project):
    import pytest

    from printsect.core.errors import ConfigError

    with pytest.raises(ConfigError, match="letters, digits"):
        project({}, config='devices:\n  "board a!": {}\n')


def test_doctor_for_an_agent_never_rereads_a_changed_build(project, tmp_path):
    import shutil

    import pytest

    if shutil.which("make") is None:
        pytest.skip("needs GNU make")
    # A person read the build once; then a Makefile changed, with a $(shell) in it.
    mark = (tmp_path / MARK).as_posix()
    p = project({"Makefile": b"all:\n\t@true\n"},
                config="build: {steps: [{for: outs, run: make}], artifacts: auto}\n")
    assert main(["deps", "--root", str(p.root)]) == 0
    (p.root / "Makefile").write_bytes(f"X := $(shell echo > {mark})\nall:\n\t@true\n".encode())
    text, _ = call(BY_NAME["doctor"], {}, str(p.root))  # for an agent: nothing approved
    assert findings(text)["build"]["status"] == "ng"  # stops, says it would need make -n
    assert not (tmp_path / MARK).exists()  # make -n never ran


def test_printsect_own_state_does_not_need_approving(project):
    p = project({"Makefile": b"all:\n"})
    approve_now(p)
    runs = p.state_dir / "runs" / "1"
    runs.mkdir(parents=True)
    (runs / "console.log").write_text("x", encoding="utf-8")
    (p.state_dir / "probes.json").write_text("{}", encoding="utf-8")
    assert not unapproved(load_project(p.root))
    (p.state_dir / "evil.mk").write_text("X = 1", encoding="utf-8")  # not printsect's own
    assert unapproved(load_project(p.root))


def test_makefile_names_in_any_case_are_covered(project):
    p = project({"MAKEFILE": b"all:\n"})
    approve_now(p)
    (p.root / "MAKEFILE").write_bytes(b"all:\n\t$(shell echo hi)\n")
    assert unapproved(load_project(p.root))


def test_a_tampered_graph_cannot_add_to_a_command(project):
    import pytest

    from printsect.core.errors import PsectError
    from printsect.deps import save
    from printsect.deps.graph import Graph
    from printsect.suite.build import plan

    p = project({"Makefile": b"all:\n"},
                config="build: {steps: [{for: outs, run: 'make {path}'}], artifacts: auto}\n")
    graph = Graph()  # as if someone had edited .printsect/deps.json by hand
    graph.objects["x.o"] = {"source": "x.c", "headers": []}
    graph.outs["app.out & calc"] = {"objects": ["x.o"], "archives": [], "libs": []}
    save(p, graph)
    with pytest.raises(PsectError, match="not a plain path inside the project"):
        plan(p, 1, files=["x.c"])


def test_a_header_with_make_syntax_is_covered_after_all(project):
    p = project({"Makefile": b"all:\n", "inc/a.h": b"#define A 1\n", "obj/x.d": b"x.o: x.c\n"})
    approve_now(p)
    (p.root / "inc" / "a.h").write_bytes(b"#define A 2\n")  # plain C: a probe's kind of edit
    (p.root / "obj" / "x.d").write_bytes(b"x.o: x.c a.h\n")  # plain dependencies
    assert not unapproved(load_project(p.root))
    (p.root / "obj" / "x.d").write_bytes(b"X := $(shell echo hi)\n")  # make syntax
    assert unapproved(load_project(p.root))


def test_a_linked_folder_is_covered(project, tmp_path):
    import pytest

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "rules.mk").write_bytes(b"A = 1\n")
    p = project({"Makefile": b"all:\n"})
    try:
        os.symlink(outside, p.root / "linked", target_is_directory=True)
    except (OSError, NotImplementedError):
        try:
            import _winapi

            _winapi.CreateJunction(str(outside), str(p.root / "linked"))
        except (ImportError, OSError):
            pytest.skip("this system makes neither links nor junctions")
    approve_now(p)
    (outside / "rules.mk").write_bytes(b"A = $(shell echo hi)\n")
    assert unapproved(load_project(p.root))


def test_only_plain_dependency_files_are_left_out(project):
    gcc_mp = b"obj/x.o: x.c inc/a.h \\\n inc/b.h\ninc/a.h:\ninc/b.h:\n"  # gcc -MMD -MP
    p = project({"Makefile": b"all:\n", "obj/x.d": gcc_mp})
    approve_now(p)
    (p.root / "obj" / "x.d").write_bytes(gcc_mp.replace(b"x.c", b"x.c y.h"))
    assert not unapproved(load_project(p.root))  # still plain: rebuilt deps need no approval
    for sneaky_d in (b"x.o: x.c\n\trm -rf build\n",  # a recipe line, no $ at all
                     b"CC = evil\n", b"export X\n", b"x.o: x.c ; touch y\n"):
        (p.root / "obj" / "x.d").write_bytes(sneaky_d)
        assert unapproved(load_project(p.root)), sneaky_d


def test_a_header_a_makefile_includes_is_covered(project):
    p = project({"Makefile": b"include cfg/rules.h\nall:\n", "cfg/rules.h": b"A = 1\n",
                 "inc/plain.h": b"#define B 1\n"})
    approve_now(p)
    (p.root / "inc" / "plain.h").write_bytes(b"#define B 2\n")  # a probe's kind of edit
    assert not unapproved(load_project(p.root))
    (p.root / "cfg" / "rules.h").write_bytes(b"x:\n\trm -rf build\n")  # make reads this one
    assert unapproved(load_project(p.root))


def test_graph_values_must_stay_inside_the_project(project):
    import pytest

    from printsect.core.errors import PsectError
    from printsect.suite.build import _safe

    for ok in ("build/out/app.out", ".", "net", "lib/libnet.a"):
        assert _safe(ok, "x") == ok
    for bad in ("C:/evil", "/etc", "../up", "a/../../up", ".git/hooks", ".printsect/x",
                ".GIT/hooks", ".git./hooks", "x/.Printsect/y",
                "-oops", "app.out\n", "a b", "a&b", "a|b", "a;b"):
        with pytest.raises(PsectError, match="not a plain path inside the project"):
            _safe(bad, "x")


def test_dependency_files_are_read_as_make_reads_them(project):
    p = project({"Makefile": b"all:\n", "x.d": b"x.o: x.c\n"})
    approve_now(p)
    for sneaky_d in (b"x.o: x.c \\\\\n\trm -rf build\n",  # two backslashes: no continuation
                     b"x.o: x.c\r\n\trm -rf build\r\n",  # CRLF
                     b"load evil.so: x\n", b"undefine X: y\n"):
        (p.root / "x.d").write_bytes(sneaky_d)
        assert unapproved(load_project(p.root)), sneaky_d


def test_sources_make_could_read_are_covered(project):
    p = project({"Makefile": b"INCLUDE A.MK\ninclude first.h\nall:\n",
                 "first.h": b"include second.h\n", "second.h": b"X = 1\n",
                 "inc/sneak.h": b"#define S 1\n"})
    approve_now(p)
    (p.root / "second.h").write_bytes(b"x:\n\trm -rf build\n")  # reached through first.h
    assert unapproved(load_project(p.root))
    approve_now(load_project(p.root))
    (p.root / "inc" / "sneak.h").write_bytes(b"X := $(shell echo hi)\n")  # make syntax
    assert unapproved(load_project(p.root))
