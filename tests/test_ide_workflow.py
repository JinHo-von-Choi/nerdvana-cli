"""Workflow tests for the IDE-style editor."""

from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.ui.app import NerdvanaApp
from nerdvana_cli.ui.editor_pane import EditorPane


@pytest.mark.asyncio
async def test_open_edit_save_flow(tmp_path: Path) -> None:
    target = tmp_path / "demo.py"
    target.write_text("value = 1\n", encoding="utf-8")

    app = NerdvanaApp(settings=NerdvanaSettings())
    app._project_root = str(tmp_path)

    async with app.run_test(size=(180, 45)) as pilot:
        await pilot.pause()
        app._open_editor_buffer("demo.py")
        await pilot.pause()

        editor = app.query_one("#editor-pane", EditorPane)
        assert "hidden" not in editor.classes
        assert editor.current_text() == "value = 1\n"

        editor.set_current_text("value = 2\n")
        assert editor.is_dirty() is True

        await pilot.press("ctrl+s")
        await pilot.pause()

        assert target.read_text(encoding="utf-8") == "value = 2\n"
        assert editor.is_dirty() is False


@pytest.mark.asyncio
async def test_escape_focuses_chat_input(tmp_path: Path) -> None:
    app = NerdvanaApp(settings=NerdvanaSettings())
    app._project_root = str(tmp_path)

    async with app.run_test(size=(180, 45)) as pilot:
        await pilot.press("ctrl+o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.query_one("#user-input").has_focus
