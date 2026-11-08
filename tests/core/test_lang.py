"""Guides and settings templates in English and Japanese, kept in step."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from printsect.cli import main
from printsect.core.config import LANGS, init_template, language, load_project
from printsect.core.errors import ConfigError
from printsect.suite.doctor import settings_problems
from printsect.suite.mcp import GUIDES, guide, instructions

SUITE = Path(__file__).parents[2] / "src" / "printsect" / "suite"


def shape(text: str) -> dict[str, int]:
    """What a translation must keep: sections, numbered steps, bullets, code spans."""
    return {
        "sections": len(re.findall(r"^## ", text, re.M)),
        "steps": len(re.findall(r"^\d+\. ", text, re.M)),
        "bullets": len(re.findall(r"^\s*- ", text, re.M)),
        "names": len(set(re.findall(r"`([a-z_]+)`", text))),
    }


@pytest.mark.parametrize("topic", ["loop", "setup"])
def test_a_translated_guide_keeps_the_english_shape(topic):
    en, ja = guide(topic, "en"), guide(topic, "ja")
    assert en != ja
    assert shape(ja) == shape(en)
    # every tool or key named in English is named in Japanese too
    assert set(re.findall(r"`([a-z_]+)`", en)) == set(re.findall(r"`([a-z_]+)`", ja))


@pytest.mark.parametrize("name", ["init_config.yaml", "init_local.yaml"])
def test_a_translated_template_has_the_same_settings(name):
    en, ja = init_template(name, "en"), init_template(name, "ja")
    assert en != ja

    def keys(value):  # the shape of a setting: its keys all the way down, not its words
        if isinstance(value, dict):
            return {k: keys(v) for k, v in value.items()}
        if isinstance(value, list):
            return [keys(v) for v in value]
        return type(value).__name__

    def examples(text: str) -> list:
        blocks = re.findall(r"^# [a-z_]+:\n(?:#  .*\n)+", text, re.M)
        return [keys(yaml.safe_load("".join(ln[2:] + "\n" for ln in b.splitlines())))
                for b in blocks]

    assert examples(ja) == examples(en)
    active = {k: v for k, v in (yaml.safe_load(ja) or {}).items() if k != "lang"}
    assert active == (yaml.safe_load(en) or {})  # the same settings, only the comments differ


@pytest.mark.parametrize("lang", LANGS)
def test_every_commented_example_is_valid_in_each_language(tmp_path, lang):
    for name, target in (("init_config.yaml", "printsect.yaml"),
                         ("init_local.yaml", "printsect.local.yaml")):
        for block in re.findall(r"^# [a-z_]+:\n(?:#  .*\n)+", init_template(name, lang), re.M):
            body = "".join(ln[2:] + "\n" for ln in block.splitlines())
            for f in ("printsect.yaml", "printsect.local.yaml"):
                (tmp_path / f).write_text("", encoding="utf-8")
            (tmp_path / target).write_text(body, encoding="utf-8")
            found = [p for p in settings_problems(load_project(tmp_path))
                     if "not a setting" in p or "expected" in p]
            assert not found, (lang, name, body, found)


def test_the_language_is_chosen_in_order(monkeypatch):
    monkeypatch.delenv("PSECT_LANG", raising=False)
    assert language() == "en"
    assert language(settings={"lang": "ja"}) == "ja"
    monkeypatch.setenv("PSECT_LANG", "ja_JP.UTF-8")
    assert language(settings={"lang": "en"}) == "ja"  # the environment over the settings
    assert language("en") == "en"  # --lang over both
    with pytest.raises(ConfigError, match="lang: one of en, ja"):
        language("fr")


def test_init_and_guide_follow_the_language(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("PSECT_LANG", raising=False)
    assert main(["init", "--lang", "ja", "--root", str(tmp_path)]) == 0
    assert "プローブ" in (tmp_path / "printsect.yaml").read_text(encoding="utf-8")
    assert load_project(tmp_path).settings["lang"] == "ja"
    capsys.readouterr()
    assert main(["guide", "setup", "--root", str(tmp_path)]) == 0  # lang: ja in the settings
    assert "設定のしかた" in capsys.readouterr().out
    assert main(["guide", "setup", "--lang", "en", "--root", str(tmp_path)]) == 0
    assert "setting up a project" in capsys.readouterr().out
    assert main(["check", "--root", str(tmp_path)]) == 0


def test_an_agent_is_told_in_the_projects_language(tmp_path):
    from printsect.suite.mcp import handle

    (tmp_path / ".printsect").mkdir()
    (tmp_path / "printsect.yaml").write_text("lang: ja\n", encoding="utf-8")
    result, _ = handle({"method": "initialize", "params": {}}, str(tmp_path))
    assert result["instructions"] == instructions("ja")
    assert "推測せず、聞く" in result["instructions"]
    answer, _ = handle({"method": "tools/call", "params": {"name": "guide",
                                                          "arguments": {"topic": "loop"}}},
                       str(tmp_path))
    assert "## ループ" in json.loads(answer["content"][0]["text"])["text"]
    for topic in GUIDES:
        assert guide(topic, "ja")
