"""The loop tells the ledger which agent made a request, on which turn and after which tool.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.loop.agent_loop import AgentLoop
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.telemetry.analytics import AnalyticsWriter, CallOrigin, PricingTable
from nerdvana_cli.core.tool import BaseTool, ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import ToolResult

PRICING = "acme:\n  priced: {input_per_1m: 1000000.0, output_per_1m: 1000000.0}\n"


class _Echo(BaseTool[Any]):
    name             = "Echo"
    description_text = "echo"

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="ok")


class _TwoSteps:
    """One tool call, then a final answer; each request reports usage."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ProviderEvent(type="tool_use_complete", tool_use_id="c1", tool_name="Echo", tool_input_complete={})
            yield ProviderEvent(type="usage", usage={"input_tokens": 100, "output_tokens": 5})
            yield ProviderEvent(type="done", stop_reason="tool_use")
        else:
            yield ProviderEvent(type="content_delta", content="done")
            yield ProviderEvent(type="usage", usage={"input_tokens": 150, "output_tokens": 7})
            yield ProviderEvent(type="done", stop_reason="end_turn")


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, origin: CallOrigin | None = None) -> tuple[AgentLoop, Path]:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: _TwoSteps())
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    pricing = tmp_path / "pricing.yml"
    pricing.write_text(PRICING, encoding="utf-8")
    table  = PricingTable(pricing_path=pricing)
    db     = tmp_path / "a.sqlite"
    registry = ToolRegistry()
    registry.register(_Echo())
    settings                = NerdvanaSettings()
    settings.cwd            = str(tmp_path)
    settings.model.provider = "acme"
    settings.model.model    = "priced"
    loop = AgentLoop(
        settings         = settings,
        registry         = registry,
        session          = SessionStorage(session_id="attr", storage_dir=str(tmp_path / "sessions")),
        analytics_writer = AnalyticsWriter(db_path=db, pricing_table=table, enabled=True),
        pricing_table    = table,
        origin           = origin,
    )
    return loop, db


def _rows(db: Path) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT agent_id, agent_type, category, parent_session_id, turn, last_tool, input_tokens FROM api_calls ORDER BY id").fetchall()
    finally:
        conn.close()


async def _drain(loop: AgentLoop) -> None:
    async for _ in loop.run("go"):
        pass


async def test_requests_carry_the_turn_and_the_tool_that_ran_before_them(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, db = _loop(monkeypatch, tmp_path)
    await _drain(loop)
    assert _rows(db) == [
        ("main", "main", "", "", 1, "", 100),
        ("main", "main", "", "", 2, "Echo", 150),
    ]


async def test_a_sub_agent_origin_is_recorded_with_its_parent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    origin   = CallOrigin(agent_id="agent_7", agent_type="Explore", category="quick", parent_session_id="parent")
    loop, db = _loop(monkeypatch, tmp_path, origin)
    await _drain(loop)
    assert {row[:4] for row in _rows(db)} == {("agent_7", "Explore", "quick", "parent")}


async def test_the_usage_listener_hears_every_request_with_its_cost(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, _ = _loop(monkeypatch, tmp_path)
    heard: list[dict[str, Any]] = []
    loop.usage_listener = heard.append
    await _drain(loop)
    assert [(e["turn"], e["last_tool"], e["input_tokens"]) for e in heard] == [(1, "", 100), (2, "Echo", 150)]
    assert heard[0]["cost_usd"] == pytest.approx(105.0)
    assert heard[0]["provider"] == "acme"


async def test_the_agent_tool_hands_category_and_parent_to_the_child() -> None:
    from unittest.mock import AsyncMock, patch

    from nerdvana_cli.core.delegation.task_state import TaskRegistry
    from nerdvana_cli.core.tool import ToolContext
    from nerdvana_cli.tools.agent_tool import AgentTool, AgentToolArgs

    registry = TaskRegistry()
    context  = ToolContext(cwd=".", task_registry=registry)
    context.state["session_id"] = "parent-1"
    with patch("nerdvana_cli.tools.agent_tool.run_subagent", new_callable=AsyncMock, return_value=("ok", 1)) as run:
        await AgentTool(settings=NerdvanaSettings(), task_registry=registry).call(
            AgentToolArgs(prompt="p", category="quick", subagent_type="Explore"), context, can_use_tool=None,
        )
    config = run.await_args.args[0]
    assert (config.category, config.parent_session_id, config.name) == ("quick", "parent-1", "Explore")
