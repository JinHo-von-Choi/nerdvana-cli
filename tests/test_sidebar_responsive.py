"""Breakpoint + Ctrl+B behavior for the responsive sidebar."""
from __future__ import annotations

import pytest

from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.ui.app import NerdvanaApp


@pytest.mark.asyncio
async def test_sidebar_auto_shown_above_breakpoint() -> None:
    app = NerdvanaApp(settings=NerdvanaSettings())
    async with app.run_test(size=(150, 40)) as pilot:
        await pilot.pause()
        sidebar = app.query_one("#sidebar")
        assert "hidden" not in sidebar.classes, "sidebar must be visible at width 150"


@pytest.mark.asyncio
async def test_sidebar_auto_hidden_below_breakpoint() -> None:
    app = NerdvanaApp(settings=NerdvanaSettings())
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        sidebar = app.query_one("#sidebar")
        assert "hidden" in sidebar.classes, "sidebar must be hidden at width 100"


@pytest.mark.asyncio
async def test_ctrl_b_toggles_sidebar_at_narrow_width() -> None:
    app = NerdvanaApp(settings=NerdvanaSettings())
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        sidebar = app.query_one("#sidebar")
        assert "hidden" in sidebar.classes
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert "hidden" not in sidebar.classes
        await pilot.press("ctrl+b")
        await pilot.pause()
        assert "hidden" in sidebar.classes
