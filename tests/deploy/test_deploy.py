from __future__ import annotations

import sys
from pathlib import Path

import pytest

from printsect.cli import main
from printsect.core import plugins
from printsect.core.errors import ConfigError
from printsect.deploy import FAILED, OK, WAITING_USER, DeployContext, DeployResult, make_deployer


def ctx(tmp_path: Path, wait_for=None, ask=None) -> DeployContext:
    return DeployContext(root=tmp_path, run=4, artifact=tmp_path / "build" / "app.out",
                         prefix="PSECT", ask=ask, wait_for=wait_for, log=lambda s: None)


def test_placeholders(tmp_path):
    c = ctx(tmp_path)
    assert c.fill("put {artifact_name} run {run}") == "put app.out run 4"
    assert c.run_banner == r"\[PSECT-RUN 4\]"


def test_default_is_none():
    assert make_deployer(None).deploy(ctx(Path("."))).status == OK


def test_unknown_type_lists_what_there_is():
    with pytest.raises(ConfigError, match="manual"):
        make_deployer({"type": "nope"})


def test_manual_goes_on_when_banner_shows(tmp_path):
    seen = []

    def wait_for(pattern, timeout):
        seen.append(pattern)
        return "[PSECT-RUN 4] started"

    d = make_deployer({"type": "manual", "message": "transfer {artifact_name}"})
    result = d.deploy(ctx(tmp_path, wait_for=wait_for))
    assert result.status == OK
    assert seen == [r"\[PSECT-RUN 4\]"]


def test_manual_without_console_or_person_waits(tmp_path):
    result = make_deployer({"type": "manual"}).deploy(ctx(tmp_path))
    assert result.status == WAITING_USER


def test_manual_asks_when_nothing_to_watch(tmp_path):
    result = make_deployer({"type": "manual"}).deploy(ctx(tmp_path, ask=lambda q: ""))
    assert result.status == OK


def test_command_runs_and_checks_exit(tmp_path):
    py = sys.executable.replace("\\", "/")
    ok = make_deployer({"type": "command", "commands": [f'"{py}" -c "print(4)"'],
                        "confirm": ""})
    assert ok.deploy(ctx(tmp_path)).status == OK
    bad = make_deployer({"type": "command", "commands": [f'"{py}" -c "raise SystemExit(2)"']})
    result = bad.deploy(ctx(tmp_path))
    assert result.status == FAILED and "exit 2" in result.message


def test_command_confirms_the_new_build_runs(tmp_path):
    py = sys.executable.replace("\\", "/")
    d = make_deployer({"type": "command", "commands": [f'"{py}" -c "pass"'],
                       "confirm_timeout": 1})
    assert d.deploy(ctx(tmp_path, wait_for=lambda p, t: None)).status == FAILED
    assert d.deploy(ctx(tmp_path, wait_for=lambda p, t: "x")).status == OK


class FakeEntryPoint:
    def __init__(self, name, obj):
        self.name, self.value, self._obj = name, "pkg.mod:Obj", obj

    def load(self):
        return self._obj


def test_plugin_adds_and_overrides(tmp_path, monkeypatch):
    class Mine:
        def __init__(self, settings):
            self.settings = settings

        def deploy(self, c):
            return DeployResult(OK, f"mine {self.settings['host']}")

    eps = {"mytool": FakeEntryPoint("mytool", Mine), "manual": FakeEntryPoint("manual", Mine)}
    monkeypatch.setattr(plugins, "_installed", lambda role: eps if role == "deployer" else {})
    assert make_deployer({"type": "mytool", "host": "h"}).deploy(ctx(tmp_path)).message == "mine h"
    assert isinstance(make_deployer({"type": "manual", "host": "h"}), Mine)
    assert plugins.available("deployer")["mytool"].startswith("plugin")


def test_cli_deploy_exit_codes(project, capsys):
    p = project({}, config="deploy:\n  type: manual\n")
    assert main(["deploy", "--json", "--root", str(p.root)]) == 3
    p = project({}, config="deploy:\n  type: none\n")
    assert main(["deploy", "--root", str(p.root)]) == 0
    assert main(["plugins", "--root", str(p.root)]) == 0
    assert "manual" in capsys.readouterr().out


def test_psect_deploy_leaves_the_build_settings_to_the_build(project):
    # build.steps and the like are not deploy's keys; it must not stop on them.
    p = project({}, config="build:\n  steps: [{run: make}]\n  artifact: app.out\n"
                           "deploy:\n  type: none\n")
    assert main(["deploy", "--root", str(p.root)]) == 0
