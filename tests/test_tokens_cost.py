"""Tests for /tokens command with accumulated cost extension."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_usage(input_tokens: int = 100, output_tokens: int = 50) -> MagicMock:
    u = MagicMock()
    u.input_tokens  = input_tokens
    u.output_tokens = output_tokens
    u.total_tokens  = input_tokens + output_tokens
    return u


def _make_state(session_id: str = "test-session") -> MagicMock:
    s = MagicMock()
    s.usage      = _make_usage()
    s.session_id = session_id
    return s


class TestHandleTokensCost:
    @pytest.mark.asyncio
    async def test_shows_basic_token_info(self) -> None:
        from nerdvana_cli.commands.session_commands import handle_tokens

        messages: list[str] = []
        app = MagicMock()
        app._add_chat_message = MagicMock(side_effect=lambda m, **kw: messages.append(m))
        app._agent_loop       = MagicMock()
        app._agent_loop.state = _make_state()

        await handle_tokens(app, "")
        assert len(messages) == 1
        assert "100" in messages[0]
        assert "50"  in messages[0]
        assert "150" in messages[0]

    @pytest.mark.asyncio
    async def test_shows_cost_when_available(self, tmp_path: Path) -> None:
        from datetime import datetime, timezone
        from nerdvana_cli.commands.session_commands import handle_tokens
        from nerdvana_cli.core.analytics import AnalyticsReader, AnalyticsWriter

        # Populate analytics with known cost
        db = tmp_path / "analytics.sqlite"
        w  = AnalyticsWriter(db_path=db, enabled=True)
        w.start_session("cost-sess")
        ts = datetime.now(timezone.utc).isoformat()
        w.record_tool_call(
            tool_name    = "Bash",
            start_ts     = ts,
            duration_ms  = 50,
            success      = True,
            provider     = "anthropic",
            model        = "claude-sonnet-4-6",
            input_tokens = 1000,
            output_tokens= 0,
        )

        messages: list[str] = []
        app = MagicMock()
        app._add_chat_message = MagicMock(side_effect=lambda m, **kw: messages.append(m))
        app._agent_loop       = MagicMock()
        app._agent_loop.state = _make_state("cost-sess")

        with patch("nerdvana_cli.commands.session_commands.AnalyticsReader") as MockReader:
            MockReader.return_value = AnalyticsReader(db_path=db)
            await handle_tokens(app, "")

        assert len(messages) == 1
        # $3.0000 from 1000 input tokens at $3/1k
        assert "$" in messages[0]
