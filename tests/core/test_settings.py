from __future__ import annotations

import json

from printsect.cli import main
from printsect.deploy.cli import main as deploy_main
from printsect.deps.cli import main as deps_main
from printsect.probe.cli import main as probe_main


def test_a_fresh_init_is_clean(tmp_path, capsys):
    assert main(["init", "--root", str(tmp_path)]) == 0
    assert main(["check", "--root", str(tmp_path)]) == 0
    assert "settings ok" in capsys.readouterr().out


def test_check_names_every_problem_with_a_near_match(project, capsys):
    p = project({}, config=(
        "deps:\n  comand: make -n\n  headers: yes please\n"
        "probe:\n  templte: x\n"
        "limits:\n  active_probes: many\n"
        "deploy:\n  type: manual\n  mesage: hi\n"
        "deplyo: {}\n"
    ))
    assert main(["check", "--json", "--root", str(p.root)]) == 2
    found = json.loads(capsys.readouterr().out)["problems"]
    text = "\n".join(found)
    assert "deplyo: not a setting (did you mean deploy?)" in text
    assert "probe.templte: not a setting (did you mean template?)" in text
    assert "limits.active_probes: expected a number" in text
    assert "deps.comand: not a setting (did you mean command?)" in text
    assert "deps.headers: expected true/false" in text
    assert "deploy.mesage: not a setting (did you mean message?)" in text


def test_each_tool_checks_its_own_settings_only(project, capsys):
    p = project({"a.c": b"int a;\n"},
                config="probe:\n  templte: x\ndeploy:\n  type: none\n  bogus: 1\n")
    root = ["--root", str(p.root)]
    assert probe_main(["list", *root]) == 2  # its own typo stops it
    assert "probe.templte" in capsys.readouterr().err
    assert deploy_main(root) == 2  # the deployer's unknown key stops it
    assert "deploy.bogus" in capsys.readouterr().err
    # psect-deps does not read probe: or deploy:, so their problems are not its business
    (p.root / "make.log").write_text("", encoding="utf-8")
    assert deps_main(["--from-log", str(p.root / "make.log"), *root]) == 0


def test_a_plugin_section_keeps_its_own_keys(project, monkeypatch):
    from printsect.core import plugins

    class Mine:
        def __init__(self, settings):
            self.settings = settings

    monkeypatch.setattr(plugins, "_installed", lambda role: {})
    plugins.register("deployer", "mine")(Mine)  # declares no SETTINGS: keys are its own
    p = project({}, config="deploy:\n  type: mine\n  host: 10.0.0.2\n")
    assert main(["check", "--root", str(p.root)]) == 0


def test_init_writes_both_files_and_never_over_one(tmp_path, capsys):
    import yaml

    from printsect.core.config import load_project

    (tmp_path / "printsect.local.yaml").write_text("# mine\n", encoding="utf-8")
    assert main(["init", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "made printsect.yaml" in out and "psect doctor" in out
    assert (tmp_path / "printsect.local.yaml").read_text(encoding="utf-8") == "# mine\n"
    text = (tmp_path / "printsect.yaml").read_text(encoding="utf-8")
    assert "\r" not in text
    # the probe template reaches C as the two characters backslash and n
    assert yaml.safe_load(text)["probe"]["template"].endswith(r'\n", __FILE__, __LINE__);')
    assert load_project(tmp_path).settings["deps"]["headers"] is True


def test_the_commented_examples_are_valid_settings(tmp_path):
    # Uncommenting an example must give settings psect check accepts.
    import re

    from printsect.core.config import init_template, load_project
    from printsect.suite.doctor import settings_problems

    for name, target in (("init_config.yaml", "printsect.yaml"),
                         ("init_local.yaml", "printsect.local.yaml")):
        examples = 0
        # An example: "# section:" and the commented lines under it, up to a blank line.
        for block in re.findall(r"^# [a-z_]+:\n(?:#  .*\n)+", init_template(name), re.M):
            examples += 1
            body = "".join(ln[2:] + "\n" for ln in block.splitlines())
            (tmp_path / "printsect.yaml").write_text("", encoding="utf-8")
            (tmp_path / "printsect.local.yaml").write_text("", encoding="utf-8")
            (tmp_path / target).write_text(body, encoding="utf-8")
            found = [p for p in settings_problems(load_project(tmp_path))
                     if "not a setting" in p or "expected" in p]
            assert not found, (name, body, found)
        assert examples >= 4  # build, run, deploy, ... / the four consoles
