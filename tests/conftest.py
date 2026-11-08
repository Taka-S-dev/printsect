from __future__ import annotations

from pathlib import Path

import pytest

from printsect.core.config import load_project


@pytest.fixture(autouse=True)
def own_user_folder(tmp_path_factory, monkeypatch):
    """Approvals and keys go to a folder of the test's own, never the user's."""
    monkeypatch.setenv("PSECT_CONFIG_DIR", str(tmp_path_factory.mktemp("user-config")))


@pytest.fixture
def project(tmp_path: Path):
    (tmp_path / ".printsect").mkdir()

    def make(files: dict[str, bytes] | None = None, config: str | None = None):
        for name, data in (files or {}).items():
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if config is not None:
            (tmp_path / "printsect.yaml").write_text(config, encoding="utf-8")
        return load_project(tmp_path)

    return make
