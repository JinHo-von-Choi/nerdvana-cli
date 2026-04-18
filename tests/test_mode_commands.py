"""Tests for /mode and /context slash commands.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nerdvana_cli.commands.profile_commands import handle_context, handle_mode
from nerdvana_cli.core.profiles import ProfileManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(cwd: str = "/tmp") -> MagicMock:
    """Build a minimal mock NerdvanaApp with profile_manager support."""
    app = MagicMock()
    app.settings.cwd                  = cwd
    app.settings.session.default_mode = "interactive"
    app.settings.session.default_context = "standalone"
    app._profile_manager              = None
    app._add_chat_message             = MagicMock()
    return app


# ---------------------------------------------------------------------------
# /mode
# ---------------------------------------------------------------------------

class TestHandleMode:
    @pytest.mark.asyncio
    async def test_mode_list_shows_modes(self) -> None:
        app = _make_app()
        await handle_mode(app, "list")
        msg = app._add_chat_message.call_args[0][0]
        assert "planning" in msg
        assert "editing" in msg

    @pytest.mark.asyncio
    async def test_mode_activate(self) -> None:
        app = _make_app()
        await handle_mode(app, "planning")
        msg = app._add_chat_message.call_args[0][0]
        assert "planning" in msg.lower()

    @pytest.mark.asyncio
    async def test_mode_off_deactivates(self) -> None:
        app = _make_app()
        await handle_mode(app, "planning")   # activate first
        await handle_mode(app, "off")        # then deactivate
        msg = app._add_chat_message.call_args[0][0]
        assert "interactive" in msg.lower()

    @pytest.mark.asyncio
    async def test_mode_unknown_reports_error(self) -> None:
        app = _make_app()
        await handle_mode(app, "nonexistent-mode-xyz")
        msg = app._add_chat_message.call_args[0][0]
        assert "error" in msg.lower() or "not found" in msg.lower()

    @pytest.mark.asyncio
    async def test_mode_empty_shows_status(self) -> None:
        app = _make_app()
        await handle_mode(app, "")
        msg = app._add_chat_message.call_args[0][0]
        assert "interactive" in msg.lower() or "mode" in msg.lower()


# ---------------------------------------------------------------------------
# /context
# ---------------------------------------------------------------------------

class TestHandleContext:
    @pytest.mark.asyncio
    async def test_context_list_shows_contexts(self) -> None:
        app = _make_app()
        await handle_context(app, "list")
        msg = app._add_chat_message.call_args[0][0]
        assert "standalone" in msg
        assert "claude-code" in msg

    @pytest.mark.asyncio
    async def test_context_activate(self) -> None:
        app = _make_app()
        await handle_context(app, "claude-code")
        msg = app._add_chat_message.call_args[0][0]
        assert "claude-code" in msg.lower()

    @pytest.mark.asyncio
    async def test_context_unknown_reports_error(self) -> None:
        app = _make_app()
        await handle_context(app, "nonexistent-context-xyz")
        msg = app._add_chat_message.call_args[0][0]
        assert "error" in msg.lower() or "not found" in msg.lower()

    @pytest.mark.asyncio
    async def test_context_empty_shows_status(self) -> None:
        app = _make_app()
        await handle_context(app, "")
        msg = app._add_chat_message.call_args[0][0]
        assert "standalone" in msg.lower() or "context" in msg.lower()

    @pytest.mark.asyncio
    async def test_context_persists_in_profile_manager(self) -> None:
        app = _make_app()
        await handle_context(app, "ide")
        pm: ProfileManager = app._profile_manager
        assert pm.active_context_name == "ide"
