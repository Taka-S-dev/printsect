"""printsect stops rather than read or write through a link in its state folder."""

from __future__ import annotations

import json
import os
import stat

import pytest

from printsect.cli import main
from printsect.core import fsutil
from printsect.core.errors import ConfigError
from printsect.deps import load
from printsect.probe.ledger import Ledger
from printsect.suite.run import run_folder, runs_dir


@pytest.fixture
def link_named(monkeypatch):
    """Make lstat call one name a link, on any PC, whether links may be made or not."""
    real = os.lstat

    def fake(name):
        def lstat(path):
            if os.path.basename(path) == name:
                return os.stat_result((stat.S_IFLNK | 0o644, 0, 0, 1, 0, 0, 0, 0, 0, 0))
            return real(path)

        monkeypatch.setattr(fsutil.os, "lstat", lstat)

    return fake


def test_real_files_and_missing_ones_are_fine(project):
    p = project({})
    assert p.state_dir.name == ".printsect"
    assert p.state_file("nothing-yet.json").name == "nothing-yet.json"
    assert Ledger(p.state_dir).probes == {}


def test_the_state_folder_itself(project, link_named):
    p = project({})
    link_named(".printsect")
    with pytest.raises(ConfigError, match="link"):
        _ = p.state_dir


@pytest.mark.parametrize("name", ["deps.json", "probes.json", "map.json", "build.log"])
def test_a_file_in_it(project, link_named, name):
    p = project({})
    link_named(name)
    with pytest.raises(ConfigError, match="link"):
        p.state_file(name)


def test_the_probe_list_is_not_read_through_a_link(project, link_named):
    p = project({})
    saved = json.dumps({"next": 1, "probes": []})
    (p.state_dir / "probes.json").write_text(saved, encoding="utf-8")
    link_named("probes.json")
    with pytest.raises(ConfigError):
        Ledger(p.state_dir)


def test_the_build_reading_is_not_read_through_a_link(project, link_named):
    p = project({})
    link_named("deps.json")
    with pytest.raises(ConfigError):
        load(p)


def test_the_folders_of_runs(project, link_named):
    p = project({})
    link_named("7")
    with pytest.raises(ConfigError):
        run_folder(p, 7)
    link_named("runs")
    with pytest.raises(ConfigError):
        runs_dir(p)


def test_the_ignore_file_is_not_written_through_a_link(tmp_path, link_named):
    link_named(".gitignore")
    with pytest.raises(ConfigError):
        fsutil.ensure_state_dir(tmp_path / ".printsect")
    assert not (tmp_path / ".printsect").exists()


def test_every_command_says_why_and_stops(project, capsys, link_named):
    p = project({})
    link_named("device")
    assert main(["check", "--root", str(p.root)]) == 2
    assert "link" in capsys.readouterr().err


def test_a_link_is_refused_for_real(project, capsys, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    p = project({})
    p.state_dir.rmdir()
    try:
        os.symlink(outside, p.root / ".printsect", target_is_directory=True)
    except OSError:
        pytest.skip("this PC does not let us make a link")
    assert main(["probe", "list", "--root", str(p.root)]) == 2
    assert not list(outside.iterdir())
