"""Each tool runs on its own: it may use printsect.core, never another tool.

Joining tools up belongs in printsect/cli.py (the `psect` command) alone. This keeps
`psect-deps` and the others usable without the rest, like separate utilities.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).parents[1] / "src" / "printsect"
TOOLS = sorted(p.name for p in PKG.iterdir() if (p / "cli.py").is_file() and p.name != "core")


def imported_tops(path: Path) -> set[str]:
    """The printsect sub-packages a module imports (relative or absolute)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 2 and node.module:  # from ..x import y
                found.add(node.module.split(".")[0])
            elif node.level == 2:  # from .. import x
                found.update(a.name for a in node.names)
            elif node.level == 0 and (node.module or "").startswith("printsect."):
                found.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("printsect."):
                    found.add(a.name.split(".")[1])
    return found


def test_there_are_tools():
    assert {"deps", "probe", "deploy"} <= set(TOOLS)


@pytest.mark.parametrize("tool", TOOLS)
def test_tool_uses_only_core(tool):
    allowed = {"core", "__version__"}
    for path in (PKG / tool).rglob("*.py"):
        bad = imported_tops(path) - allowed
        assert not bad, f"{path.relative_to(PKG)} imports {sorted(bad)}; go through core or psect"


def test_core_uses_no_tool():
    for path in (PKG / "core").rglob("*.py"):
        assert not imported_tops(path) & set(TOOLS), path


@pytest.mark.parametrize("tool", TOOLS)
def test_tool_starts_alone(tool):
    # A fresh interpreter: importing one tool's command must not pull in another tool.
    code = (
        f"import sys, printsect.{tool}.cli as m; m.build_parser(); "
        f"others = [t for t in {TOOLS!r} if t != {tool!r} and 'printsect.' + t in sys.modules]; "
        "print(others)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]"


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("readme", ["README.md", "README.ja.md"])
def test_tool_has_a_guide_that_matches_its_commands(tool, readme):
    import argparse
    import importlib

    guide = (PKG / tool / readme).read_text(encoding="utf-8")
    parser = importlib.import_module(f"printsect.{tool}.cli").build_parser()
    actions = [
        name
        for a in parser._actions if isinstance(a, argparse._SubParsersAction)
        for name in a.choices
    ]
    for name in actions:
        assert f"psect-{tool} {name}" in guide, f"{tool}/{readme} does not show `{name}`"
    for path in (PKG / tool).glob("*.py"):
        if path.name not in ("__init__.py", "__main__.py"):
            assert f"`{path.name}`" in guide, f"{tool}/{readme} does not list {path.name}"


@pytest.mark.parametrize("folder", [PKG.parents[1], *(PKG / t for t in TOOLS), PKG / "core"],
                         ids=lambda f: f.name)
def test_every_readme_has_its_japanese_twin(folder):
    en = (folder / "README.md").read_text(encoding="utf-8")
    ja = (folder / "README.ja.md").read_text(encoding="utf-8")
    assert "[日本語](README.ja.md)" in en and "[English](README.md)" in ja
    # The same sections, tables and examples: a translation, not a summary.
    for mark in (r"^## ", r"^\| ", r"^```"):
        import re

        assert len(re.findall(mark, en, re.M)) == len(re.findall(mark, ja, re.M)), mark
