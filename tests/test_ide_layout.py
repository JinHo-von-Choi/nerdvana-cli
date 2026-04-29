"""Layout tests for IDE-style panes."""

from __future__ import annotations

import pytest

from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.ui.app import NerdvanaApp


@pytest.mark.asyncio
async def test_ide_panes_exist_and_start_hidden() -> None:
    app = NerdvanaApp(settings=NerdvanaSettings())
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()

        tree_pane = app.query_one("#project-tree-pane")
        edit_pane = app.query_one("#editor-pane")

        assert "hidden" in tree_pane.classes
        assert "hidden" in edit_pane.classes


@pytest.mark.asyncio
async def test_ctrl_e_toggles_project_tree() -> None:
    app = NerdvanaApp(settings=NerdvanaSettings())
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        pane = app.query_one("#project-tree-pane")
        assert "hidden" in pane.classes

        await pilot.press("ctrl+e")
        await pilot.pause()

        assert "hidden" not in pane.classes


@pytest.mark.asyncio
async def test_ctrl_o_toggles_editor() -> None:
    app = NerdvanaApp(settings=NerdvanaSettings())
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        pane = app.query_one("#editor-pane")
        assert "hidden" in pane.classes

        await pilot.press("ctrl+o")
        await pilot.pause()

        assert "hidden" not in pane.classes
