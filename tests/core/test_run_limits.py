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


async def test_the_token_limit_stops_the_run_for_a_model_without_a_price(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Endless(usage={"input_tokens": 600, "output_tokens": 100})
    loop     = _loop(monkeypatch, tmp_path, provider, model="unpriced", max_total_tokens=2_000)
    output   = await _drain(loop)
    assert loop.last_stop == "max_total_tokens"
    assert provider.calls == 3
    assert "Token limit reached (2,100 of 2,000)" in output


async def test_no_token_limit_means_tokens_never_stop_a_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Endless(usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000})
    loop     = _loop(monkeypatch, tmp_path, provider, max_turns=3)
    await _drain(loop)
    assert loop.last_stop == "max_turns"


async def test_require_price_refuses_before_the_first_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Endless(usage={"input_tokens": 10, "output_tokens": 1})
    loop     = _loop(monkeypatch, tmp_path, provider, model="unpriced", max_cost_usd=1.0, require_price=True)
    output   = await _drain(loop)
    assert loop.last_stop == "unpriced"
    assert provider.calls == 0
    assert "Refusing to run" in output


async def test_require_price_does_nothing_without_a_cost_limit_or_with_a_priced_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for model, limit in (("unpriced", 0.0), ("priced", 5.0)):
        provider = _Once([ProviderEvent(type="done", stop_reason="end_turn")])
        loop     = _loop(monkeypatch, tmp_path, provider, model=model, max_cost_usd=limit, require_price=True)
        await _drain(loop)
        assert loop.last_stop == "completed"


def test_the_new_stop_reasons_map_to_exit_codes() -> None:
    from nerdvana_cli.cli.run_output import EXIT_BUDGET, EXIT_CONFIG, RunResult

    assert RunResult(stop="max_total_tokens").exit_code == EXIT_BUDGET
    assert RunResult(stop="unpriced").exit_code == EXIT_CONFIG
    assert RunResult(stop="unpriced").to_dict()["subtype"] == "error_unpriced"


async def test_the_loop_counts_a_todo_nudge_and_a_provider_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from nerdvana_cli.core import signals

    loop = _loop(monkeypatch, tmp_path, _Once([ProviderEvent(type="done", stop_reason="end_turn")]))
    loop._signals[signals.PROVIDER_RETRY] += 2
    loop.tool_executor.signals[signals.CAS_REJECTED] += 1
    assert loop.signal_summary() == {"cas_rejected": 1, "provider_retry": 2}


class _Recording:
    """Calls a tool every request and keeps what it was sent."""

    def __init__(self) -> None:
        self.payloads: list[list[dict[str, Any]]] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.payloads.append([dict(m) for m in messages])
        n = len(self.payloads)
        yield ProviderEvent(type="tool_use_complete", tool_use_id=f"c{n}", tool_name="Echo", tool_input_complete={"n": n})
        yield ProviderEvent(type="done", stop_reason="tool_use")


async def test_a_loop_told_to_wrap_up_gets_the_reminder_once_at_that_turn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Recording()
    loop     = _loop(monkeypatch, tmp_path, provider, max_turns=4)
    loop.wrap_up_at = 3
    await _drain(loop)
    sent = [[str(m.get("content", "")) for m in payload if m["role"] == "user"] for payload in provider.payloads]
    assert not any("Turn budget" in text for text in sent[0] + sent[1])
    assert sum("Turn budget" in text for text in sent[2]) == 1
    assert sum("Turn budget" in text for text in sent[3]) == 1  # the same message stays in the history, it is not added again
    assert "2 of 4 turns are used" in "".join(sent[2])
    assert loop.signal_summary()["wrap_up"] == 1


async def test_without_a_wrap_up_turn_nothing_is_added(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Recording()
    loop     = _loop(monkeypatch, tmp_path, provider, max_turns=3)
    await _drain(loop)
    assert not any("Turn budget" in str(m.get("content", "")) for payload in provider.payloads for m in payload)


async def test_run_subagent_sets_the_wrap_up_turn_from_the_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import asyncio

    from nerdvana_cli.core.subagent import run_subagent
    from nerdvana_cli.core.subagent_config import SubagentConfig

    seen: list[int] = []
    original = AgentLoop.run

    async def spy(self: AgentLoop, prompt: str) -> AsyncIterator[str]:
        seen.append(self.wrap_up_at)
        async for chunk in original(self, prompt):
            yield chunk

    monkeypatch.setattr(AgentLoop, "run", spy)
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: _Once([ProviderEvent(type="done", stop_reason="end_turn")]))
    settings = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    for fraction, expected in ((0.6, 12), (0.0, 0)):
        await run_subagent(
            SubagentConfig(agent_id="a", name="Explore", prompt="p", settings=settings, registry=ToolRegistry(), max_turns=20, wrap_up_fraction=fraction),
            asyncio.Event(),
        )
        assert seen[-1] == expected


async def test_a_sub_agent_that_hits_its_share_returns_what_it_had_and_says_so(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import asyncio

    from nerdvana_cli.core.subagent import run_subagent
    from nerdvana_cli.core.subagent_config import SubagentConfig

    provider = _Endless(usage={"input_tokens": 1_000_000, "output_tokens": 0})
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    pricing = tmp_path / "pricing.yml"
    pricing.write_text(PRICING, encoding="utf-8")
    monkeypatch.setattr("nerdvana_cli.core.run_limits.PricingTable", lambda: PricingTable(pricing_path=pricing))
    settings = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    settings.model.provider = "acme"
    settings.model.model    = "priced"
    settings.session.max_cost_usd = 2_000_000.0
    registry = ToolRegistry()
    registry.register(_Echo())
    config = SubagentConfig(agent_id="a", name="Explore", prompt="p", settings=settings, registry=registry, max_turns=20)
    output, tokens = await run_subagent(config, asyncio.Event())
    assert config.stopped_for == "max_cost"
    assert config.cost_usd == pytest.approx(2_000_000.0)
    assert "used its share of the cost budget" in output
    assert tokens == 2_000_000          # the tokens of every request, not only the last one
