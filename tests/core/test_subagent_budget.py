"""A session's cost limit is shared with its sub-agents as envelopes that are reserved, spent and settled.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.delegation.task_state import TaskRegistry
from nerdvana_cli.core.loop.subagent_config import SubagentConfig
from nerdvana_cli.core.state.budget import MIN_ENVELOPE, Budget
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.agent_tool import AgentTool, AgentToolArgs

# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


def test_an_envelope_is_a_share_of_what_is_left() -> None:
    budget = Budget(limit=10.0)
    first  = budget.reserve(0.5, own_spend=2.0)
    assert first.amount == pytest.approx(4.0)        # half of 10 - 2
    second = budget.reserve(0.5, own_spend=2.0)
    assert second.amount == pytest.approx(2.0)       # half of what is left after the first promise
    assert budget.remaining(2.0) == pytest.approx(2.0)


def test_settling_charges_the_actual_spend_and_returns_the_rest() -> None:
    budget   = Budget(limit=10.0)
    envelope = budget.reserve(0.5, 0.0)
    budget.settle(envelope, 1.25)
    assert budget.spent == pytest.approx(1.25) and budget.promised == pytest.approx(0.0)
    assert budget.remaining(0.0) == pytest.approx(8.75)


def test_settling_twice_charges_once() -> None:
    budget   = Budget(limit=10.0)
    envelope = budget.reserve(0.5, 0.0)
    budget.settle(envelope, 1.0)
    budget.settle(envelope, 1.0)
    assert budget.spent == pytest.approx(1.0)


def test_an_exhausted_budget_still_hands_out_a_token_envelope_and_never_goes_negative() -> None:
    budget = Budget(limit=1.0)
    assert budget.remaining(5.0) == 0.0
    assert budget.reserve(0.5, 5.0).amount == pytest.approx(MIN_ENVELOPE)


def test_the_fraction_is_clamped() -> None:
    budget = Budget(limit=4.0)
    assert budget.reserve(7.0, 0.0).amount == pytest.approx(4.0)
    assert Budget(limit=4.0).reserve(-1.0, 0.0).amount == pytest.approx(MIN_ENVELOPE)


# ---------------------------------------------------------------------------
# Through the Agent tool
# ---------------------------------------------------------------------------


def _context(budget: Budget | None, own_spend: float = 0.0) -> ToolContext:
    registry = TaskRegistry()
    context  = ToolContext(cwd=".", task_registry=registry)
    if budget is not None:
        context.state["budget"] = (budget, lambda: own_spend)
    return context


async def _call(settings: NerdvanaSettings, context: ToolContext, spend: float = 0.0) -> SubagentConfig:
    async def fake(config: SubagentConfig, abort: object) -> tuple[str, int]:
        config.cost_usd = spend
        return "done", 10

    registry = TaskRegistry()
    with patch("nerdvana_cli.tools.agent_tool.run_subagent", new=AsyncMock(side_effect=fake)) as run:
        await AgentTool(settings=settings, task_registry=registry).call(AgentToolArgs(prompt="p"), context, can_use_tool=None)
    return run.await_args.args[0]  # type: ignore[no-any-return]


async def test_the_child_gets_its_share_of_the_parents_limit_and_the_spend_is_charged_back() -> None:
    settings = NerdvanaSettings()
    settings.session.max_cost_usd = 10.0
    budget   = Budget(limit=10.0)
    config   = await _call(settings, _context(budget, own_spend=2.0), spend=1.5)
    assert config.settings.session.max_cost_usd == pytest.approx(4.0)
    assert budget.spent == pytest.approx(1.5) and budget.promised == pytest.approx(0.0)


async def test_without_a_parent_limit_or_with_a_zero_fraction_nothing_is_reserved() -> None:
    settings = NerdvanaSettings()
    config   = await _call(settings, _context(Budget(limit=0.0)))
    assert config.settings.session.max_cost_usd == 0.0
    settings.session.subagent_budget_fraction = 0.0
    budget = Budget(limit=10.0)
    config = await _call(settings, _context(budget))
    assert config.settings.session.max_cost_usd == settings.session.max_cost_usd
    assert budget.promised == 0.0
    config = await _call(NerdvanaSettings(), _context(None))
    assert config.settings.session.max_cost_usd == 0.0


async def test_a_failing_child_still_settles_its_envelope() -> None:
    settings = NerdvanaSettings()
    settings.session.max_cost_usd = 10.0
    budget   = Budget(limit=10.0)
    context  = _context(budget)
    registry = TaskRegistry()
    with patch("nerdvana_cli.tools.agent_tool.run_subagent", new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = await AgentTool(settings=settings, task_registry=registry).call(AgentToolArgs(prompt="p"), context, can_use_tool=None)
    assert "agent error" in result.content
    assert budget.promised == pytest.approx(0.0)


async def test_parallel_children_cannot_promise_the_same_money_twice() -> None:
    import asyncio

    settings = NerdvanaSettings()
    settings.session.max_cost_usd = 8.0
    budget   = Budget(limit=8.0)
    granted: list[float] = []

    async def fake(config: SubagentConfig, abort: object) -> tuple[str, int]:
        granted.append(config.settings.session.max_cost_usd)
        await asyncio.sleep(0.01)
        return "done", 1

    registry = TaskRegistry()
    tool     = AgentTool(settings=settings, task_registry=registry)
    with patch("nerdvana_cli.tools.agent_tool.run_subagent", new=AsyncMock(side_effect=fake)):
        await asyncio.gather(*[tool.call(AgentToolArgs(prompt="p"), _context_with(budget), can_use_tool=None) for _ in range(3)])
    assert sum(granted) < 8.0
    assert granted == sorted(granted, reverse=True)


def _context_with(budget: Budget) -> ToolContext:
    return _context(budget)


# ---------------------------------------------------------------------------
# The loops
# ---------------------------------------------------------------------------


async def test_the_parents_limit_counts_what_its_sub_agents_spent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from collections.abc import AsyncIterator
    from typing import Any

    from nerdvana_cli.core.loop.agent_loop import AgentLoop
    from nerdvana_cli.core.state.session import SessionStorage
    from nerdvana_cli.core.telemetry.analytics import AnalyticsWriter, PricingTable
    from nerdvana_cli.core.tool import ToolRegistry
    from nerdvana_cli.providers.base import ProviderEvent

    class _Silent:
        async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
            yield ProviderEvent(type="content_delta", content="x")
            yield ProviderEvent(type="done", stop_reason="end_turn")

    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: _Silent())
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    settings = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    settings.session.max_cost_usd = 3.0
    table = PricingTable(pricing_path=tmp_path / "none.yml")
    loop  = AgentLoop(settings=settings, registry=ToolRegistry(), session=SessionStorage(session_id="b", storage_dir=str(tmp_path / "s")),
                      analytics_writer=AnalyticsWriter(db_path=tmp_path / "a.sqlite", pricing_table=table, enabled=True), pricing_table=table)
    assert loop.limits.over_cost_limit() == ""
    loop.budget.spent = 3.5
    assert "Cost limit reached" in loop.limits.over_cost_limit()
    settings.session.max_cost_usd = 5.0            # a new limit starts a fresh ledger
    assert loop.budget.limit == 5.0 and loop.budget.spent == 0.0
    context = loop._new_tool_context()
    assert context.state["budget"][0] is loop.budget
