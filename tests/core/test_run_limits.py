"""Stop reasons, turns used and the session cost limit of the agent loop.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.analytics import AnalyticsWriter, PricingTable
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import BaseTool, ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import ToolResult

PRICING = """\
acme:
  priced: {input_per_1m: 1000000.0, output_per_1m: 1000000.0}
"""


class _Echo(BaseTool[Any]):
    name             = "Echo"
    description_text = "echo"

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="ok")


class _Endless:
    """Calls a tool on every request, with different arguments each time."""

    def __init__(self, usage: dict[str, int] | None = None) -> None:
        self.calls = 0
        self.usage = usage

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        yield ProviderEvent(type="tool_use_complete", tool_use_id=f"c{self.calls}", tool_name="Echo", tool_input_complete={"n": self.calls})
        if self.usage:
            yield ProviderEvent(type="usage", usage=dict(self.usage))
        yield ProviderEvent(type="done", stop_reason="tool_use")


class _Once:
    def __init__(self, events: list[ProviderEvent]) -> None:
        self.events = events

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        for event in self.events:
            yield event


def _loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path:    Path,
    provider:    Any,
    model:       str = "priced",
    **session:   Any,
) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    pricing_path = tmp_path / "pricing.yml"
    pricing_path.write_text(PRICING, encoding="utf-8")
    table = PricingTable(pricing_path=pricing_path)
    registry = ToolRegistry()
    registry.register(_Echo())
    settings                = NerdvanaSettings()
    settings.cwd            = str(tmp_path)
    settings.model.provider = "acme"
    settings.model.model    = model
    for key, value in session.items():
        setattr(settings.session, key, value)
    return AgentLoop(
        settings         = settings,
        registry         = registry,
        session          = SessionStorage(session_id="limits", storage_dir=str(tmp_path / "sessions")),
        analytics_writer = AnalyticsWriter(db_path=tmp_path / "a.sqlite", pricing_table=table, enabled=True),
        pricing_table    = table,
    )


async def _drain(loop: AgentLoop, prompt: str = "go") -> str:
    return "".join([chunk async for chunk in loop.run(prompt)])


async def test_a_normal_finish_is_completed_after_one_turn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop = _loop(monkeypatch, tmp_path, _Once([ProviderEvent(type="content_delta", content="hi"), ProviderEvent(type="done", stop_reason="end_turn")]))
    await _drain(loop)
    assert (loop.last_stop, loop.turns_used) == ("completed", 1)


async def test_the_turn_limit_is_reported_with_the_turns_used(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Endless()
    loop     = _loop(monkeypatch, tmp_path, provider, max_turns=3)
    output   = await _drain(loop)
    assert loop.last_stop == "max_turns"
    assert loop.turns_used == 3
    assert provider.calls == 3
    assert "Max turns (3) reached" in output


async def test_a_provider_failure_is_reported_as_such(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop = _loop(monkeypatch, tmp_path, _Once([ProviderEvent(type="error", error="bad request", error_kind="other")]))
    await _drain(loop)
    assert loop.last_stop == "provider_error"


async def test_the_status_resets_on_the_next_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Endless()
    loop     = _loop(monkeypatch, tmp_path, provider, max_turns=2)
    await _drain(loop)
    assert loop.last_stop == "max_turns"
    loop.provider = _Once([ProviderEvent(type="done", stop_reason="end_turn")])
    await _drain(loop, "again")
    assert (loop.last_stop, loop.turns_used) == ("completed", 1)


async def test_the_cost_limit_stops_the_run_before_the_next_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Each request reports 1,000,000 prompt tokens at 1,000,000 USD per 1M tokens.
    provider = _Endless(usage={"input_tokens": 1_000_000, "output_tokens": 0})
    loop     = _loop(monkeypatch, tmp_path, provider, max_cost_usd=5_000_000.0)
    output   = await _drain(loop)
    assert loop.last_stop == "max_cost"
    assert provider.calls == 5
    assert "Cost limit reached" in output
    assert loop.session_cost_usd() == pytest.approx(5_000_000.0)


async def test_no_limit_means_the_cost_never_stops_a_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Endless(usage={"input_tokens": 1_000_000, "output_tokens": 0})
    loop     = _loop(monkeypatch, tmp_path, provider, max_turns=4)
    await _drain(loop)
    assert loop.last_stop == "max_turns"
    assert provider.calls == 4


async def test_a_limit_without_a_known_price_warns_once_and_does_not_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Endless(usage={"input_tokens": 1_000_000, "output_tokens": 0})
    loop     = _loop(monkeypatch, tmp_path, provider, model="unpriced", max_cost_usd=1.0, max_turns=3)
    output   = await _drain(loop)
    assert output.count("is not enforced") == 1
    assert loop.last_stop == "max_turns"


async def test_usage_summary_totals_every_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Endless(usage={"input_tokens": 100, "output_tokens": 7, "cache_read_tokens": 60, "cache_write_tokens": 10})
    loop     = _loop(monkeypatch, tmp_path, provider, max_turns=2)
    await _drain(loop)
    assert loop.usage_summary() == {"input_tokens": 200, "output_tokens": 14, "cache_read_tokens": 120, "cache_write_tokens": 20}
