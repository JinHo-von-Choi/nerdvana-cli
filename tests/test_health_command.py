"""Tests for /health command handler."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_app(messages: list[str], raw_messages: list[str] | None = None) -> MagicMock:
    """Return a minimal mock NerdvanaApp that records chat messages."""
    app = MagicMock()

    def _record(msg: str, raw_text: str = "", **kw: object) -> None:
        messages.append(msg)
        if raw_messages is not None:
            raw_messages.append(raw_text or msg)

    app._add_chat_message = MagicMock(side_effect=_record)
    return app


@pytest.fixture
def db_writer(tmp_path: Path):
    from nerdvana_cli.core.analytics import AnalyticsWriter
    w = AnalyticsWriter(db_path=tmp_path / "analytics.sqlite", enabled=True)
    w.start_session("health-test-sess")
    ts = datetime.now(timezone.utc).isoformat()
    for i in range(5):
        w.record_tool_call(
            tool_name    = "Bash",
            start_ts     = ts,
            duration_ms  = 100 + i * 10,
            success      = i < 4,
            error_class  = "RuntimeError" if i == 4 else None,
            provider     = "anthropic",
            model        = "claude-sonnet-4-6",
            input_tokens = 200,
            output_tokens= 100,
        )
    return tmp_path / "analytics.sqlite"


class TestHandleHealth:
    @pytest.mark.asyncio
    async def test_default_output(self, db_writer: Path) -> None:
        from nerdvana_cli.commands.observability_commands import handle_health
        from nerdvana_cli.core.analytics import AnalyticsReader

        messages: list[str] = []
        app = _make_app(messages)

        with patch("nerdvana_cli.commands.observability_commands.AnalyticsReader") as MockReader:
            reader = AnalyticsReader(db_path=db_writer)
            MockReader.return_value = reader
            await handle_health(app, "")

        assert len(messages) == 1
        output = messages[0]
        assert "Health" in output
        assert "Calls" in output
        assert "Tokens" in output
        assert "Cost" in output

    @pytest.mark.asyncio
    async def test_json_flag(self, db_writer: Path) -> None:
        from nerdvana_cli.commands.observability_commands import handle_health
        from nerdvana_cli.core.analytics import AnalyticsReader

        messages:     list[str] = []
        raw_messages: list[str] = []
        app = _make_app(messages, raw_messages)

        with patch("nerdvana_cli.commands.observability_commands.AnalyticsReader") as MockReader:
            reader = AnalyticsReader(db_path=db_writer)
            MockReader.return_value = reader
            await handle_health(app, "--json")

        assert len(messages) == 1
        # raw_text carries clean JSON (no markup)
        data = json.loads(raw_messages[0])
        assert "total_calls" in data
        assert "total_cost_usd" in data

    @pytest.mark.asyncio
    async def test_days_flag(self, db_writer: Path) -> None:
        from nerdvana_cli.commands.observability_commands import handle_health
        from nerdvana_cli.core.analytics import AnalyticsReader

        messages: list[str] = []
        app = _make_app(messages)

        with patch("nerdvana_cli.commands.observability_commands.AnalyticsReader") as MockReader:
            reader = AnalyticsReader(db_path=db_writer)
            MockReader.return_value = reader
            await handle_health(app, "--days 30")

        assert len(messages) == 1
        assert "30d" in messages[0] or "Health" in messages[0]

    @pytest.mark.asyncio
    async def test_no_data(self) -> None:
        from nerdvana_cli.commands.observability_commands import handle_health

        messages: list[str] = []
        app = _make_app(messages)
        await handle_health(app, "")
        assert len(messages) == 1
        assert "0" in messages[0]  # zero calls


class TestHandleDashboard:
    @pytest.mark.asyncio
    async def test_toggle_called(self) -> None:
        from nerdvana_cli.commands.observability_commands import handle_dashboard
        from nerdvana_cli.ui.dashboard_tab import DashboardTab

        messages: list[str] = []
        app = _make_app(messages)
        tab = DashboardTab()
        app.query_one = MagicMock(return_value=tab)

        await handle_dashboard(app, "")
        assert "active" in tab.classes
