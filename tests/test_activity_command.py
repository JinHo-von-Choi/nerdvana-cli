"""Tests for /activity slash command.

작성자: 최진호
작성일: 2026-04-29
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nerdvana_cli.commands.system_commands import handle_activity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(show_activity: bool = True) -> MagicMock:
    """Build a minimal mock NerdvanaApp for activity command tests."""
    app = MagicMock()
    app.settings.session.show_activity = show_activity
    app._add_chat_message              = MagicMock()
    return app


def _make_widget(display: str = "block") -> MagicMock:
    widget         = MagicMock()
    widget.styles  = MagicMock()
    widget.styles.display = display
    return widget


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHandleActivity:
    @pytest.mark.asyncio
    async def test_empty_args_shows_current_state_on(self) -> None:
        app = _make_app(show_activity=True)
        await handle_activity(app, "")
        msg = app._add_chat_message.call_args[0][0]
        assert "on" in msg

    @pytest.mark.asyncio
    async def test_empty_args_shows_current_state_off(self) -> None:
        app = _make_app(show_activity=False)
        await handle_activity(app, "")
        msg = app._add_chat_message.call_args[0][0]
        assert "off" in msg

    @pytest.mark.asyncio
    async def test_on_enables_activity(self) -> None:
        app    = _make_app(show_activity=False)
        widget = _make_widget(display="none")
        app.query_one.return_value = widget

        with (
            patch("nerdvana_cli.core.setup.load_config", return_value={}),
            patch("nerdvana_cli.core.setup.save_config") as mock_save,
        ):
            await handle_activity(app, "on")

        assert app.settings.session.show_activity is True
        assert widget.styles.display == "block"
        msg = app._add_chat_message.call_args[0][0]
        assert "enabled" in msg
        mock_save.assert_called_once()
        saved_cfg = mock_save.call_args[0][0]
        assert saved_cfg["session"]["show_activity"] is True

    @pytest.mark.asyncio
    async def test_off_disables_activity(self) -> None:
        app    = _make_app(show_activity=True)
        widget = _make_widget(display="block")
        app.query_one.return_value = widget

        with (
            patch("nerdvana_cli.core.setup.load_config", return_value={}),
            patch("nerdvana_cli.core.setup.save_config") as mock_save,
        ):
            await handle_activity(app, "off")

        assert app.settings.session.show_activity is False
        assert widget.styles.display == "none"
        msg = app._add_chat_message.call_args[0][0]
        assert "disabled" in msg
        saved_cfg = mock_save.call_args[0][0]
        assert saved_cfg["session"]["show_activity"] is False

    @pytest.mark.asyncio
    async def test_invalid_arg_shows_usage_and_no_state_change(self) -> None:
        app = _make_app(show_activity=True)
        await handle_activity(app, "bar")
        msg = app._add_chat_message.call_args[0][0]
        assert "Usage" in msg or "usage" in msg
        assert app.settings.session.show_activity is True

    @pytest.mark.asyncio
    async def test_query_one_failure_silent_fallback(self) -> None:
        app = _make_app(show_activity=True)
        app.query_one.side_effect = Exception("widget not found")

        with (
            patch("nerdvana_cli.core.setup.load_config", return_value={}),
            patch("nerdvana_cli.core.setup.save_config"),
        ):
            await handle_activity(app, "off")

        assert app.settings.session.show_activity is False
        msg = app._add_chat_message.call_args[0][0]
        assert "disabled" in msg

    @pytest.mark.asyncio
    async def test_save_config_exception_shows_warning(self) -> None:
        app = _make_app(show_activity=True)
        app.query_one.return_value = _make_widget()

        with (
            patch("nerdvana_cli.core.setup.load_config", side_effect=OSError("disk full")),
            patch("nerdvana_cli.core.setup.save_config"),
        ):
            await handle_activity(app, "off")

        calls = [c[0][0] for c in app._add_chat_message.call_args_list]
        assert any("Config save failed" in c or "yellow" in c for c in calls)

    @pytest.mark.asyncio
    async def test_whitespace_args_treated_as_empty(self) -> None:
        app = _make_app(show_activity=False)
        await handle_activity(app, "   ")
        msg = app._add_chat_message.call_args[0][0]
        assert "Activity indicator" in msg
