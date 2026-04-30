"""Tests for /thinking slash command.

작성자: 최진호
작성일: 2026-04-29
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nerdvana_cli.commands.system_commands import handle_thinking

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(show_thinking: bool = True) -> MagicMock:
    """Build a minimal mock NerdvanaApp for thinking command tests."""
    app = MagicMock()
    app.settings.model.show_thinking = show_thinking
    app._add_chat_message             = MagicMock()
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHandleThinking:
    @pytest.mark.asyncio
    async def test_empty_args_shows_current_state_on(self) -> None:
        app = _make_app(show_thinking=True)
        await handle_thinking(app, "")
        msg = app._add_chat_message.call_args[0][0]
        assert "on" in msg

    @pytest.mark.asyncio
    async def test_empty_args_shows_current_state_off(self) -> None:
        app = _make_app(show_thinking=False)
        await handle_thinking(app, "")
        msg = app._add_chat_message.call_args[0][0]
        assert "off" in msg

    @pytest.mark.asyncio
    async def test_on_enables_thinking(self) -> None:
        app = _make_app(show_thinking=False)
        with patch("nerdvana_cli.commands.system_commands.handle_thinking.__module__"):
            pass
        with (
            patch("nerdvana_cli.core.setup.load_config", return_value={}),
            patch("nerdvana_cli.core.setup.save_config") as mock_save,
        ):
            await handle_thinking(app, "on")

        assert app.settings.model.show_thinking is True
        msg = app._add_chat_message.call_args[0][0]
        assert "enabled" in msg
        mock_save.assert_called_once()
        saved_cfg = mock_save.call_args[0][0]
        assert saved_cfg["model"]["show_thinking"] is True

    @pytest.mark.asyncio
    async def test_off_disables_thinking(self) -> None:
        app = _make_app(show_thinking=True)
        with (
            patch("nerdvana_cli.core.setup.load_config", return_value={}),
            patch("nerdvana_cli.core.setup.save_config") as mock_save,
        ):
            await handle_thinking(app, "off")

        assert app.settings.model.show_thinking is False
        msg = app._add_chat_message.call_args[0][0]
        assert "disabled" in msg
        saved_cfg = mock_save.call_args[0][0]
        assert saved_cfg["model"]["show_thinking"] is False

    @pytest.mark.asyncio
    async def test_invalid_arg_shows_usage_and_no_state_change(self) -> None:
        app = _make_app(show_thinking=True)
        await handle_thinking(app, "foo")
        msg = app._add_chat_message.call_args[0][0]
        assert "Usage" in msg or "usage" in msg
        assert app.settings.model.show_thinking is True

    @pytest.mark.asyncio
    async def test_save_config_exception_shows_warning(self) -> None:
        app = _make_app(show_thinking=True)
        with (
            patch("nerdvana_cli.core.setup.load_config", side_effect=OSError("disk full")),
            patch("nerdvana_cli.core.setup.save_config"),
        ):
            await handle_thinking(app, "off")

        calls = [c[0][0] for c in app._add_chat_message.call_args_list]
        assert any("Config save failed" in c or "yellow" in c for c in calls)

    @pytest.mark.asyncio
    async def test_whitespace_args_treated_as_empty(self) -> None:
        app = _make_app(show_thinking=True)
        await handle_thinking(app, "   ")
        msg = app._add_chat_message.call_args[0][0]
        assert "Thinking display" in msg
