"""Tests for editor file IO safety."""

from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.ui.app import NerdvanaApp


def test_save_editor_buffer_writes_relative_file(tmp_path: Path) -> None:
    app = NerdvanaApp(settings=NerdvanaSettings())
    app._project_root = str(tmp_path)

    app._save_editor_buffer("demo.py", "value = 2\n")

    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "value = 2\n"


def test_save_editor_buffer_blocks_parent_traversal(tmp_path: Path) -> None:
    app = NerdvanaApp(settings=NerdvanaSettings())
    app._project_root = str(tmp_path)

    with pytest.raises(PermissionError):
        app._save_editor_buffer("../demo.py", "blocked\n")


def test_save_editor_buffer_blocks_absolute_path(tmp_path: Path) -> None:
    app = NerdvanaApp(settings=NerdvanaSettings())
    app._project_root = str(tmp_path)

    with pytest.raises(PermissionError):
        app._save_editor_buffer(str(tmp_path / "demo.py"), "blocked\n")


def test_read_editor_buffer_reads_relative_file(tmp_path: Path) -> None:
    target = tmp_path / "demo.py"
    target.write_text("value = 1\n", encoding="utf-8")
    app = NerdvanaApp(settings=NerdvanaSettings())
    app._project_root = str(tmp_path)

    assert app._read_editor_buffer("demo.py") == "value = 1\n"
