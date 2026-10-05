"""The non-streaming resend: the limits it is held to, how it gives up, and what it must not run.

Author: 최진호
Date:   2026-10-05
"""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.loop.agent_loop import AgentLoop
from nerdvana_cli.core.loop.model_failover import _MAX_RESEND_ROUNDS
from nerdvana_cli.core.loop.run_limits import RunLimits
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.telemetry.analytics import AnalyticsWriter, PricingTable
from nerdvana_cli.core.tool import BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import Role, ToolResult

PRICING = """\
acme:
  priced: {input_per_1m: 1000000.0, output_per_1m: 1000000.0}
"""


class _Count(BaseTool[Any]):
    """How often a tool call reached it."""

    name             = "Count"
    description_text = "count"

    def __init__(self) -> None:
        self.calls = 0

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        self.calls += 1
        return ToolResult(tool_use_id="", content="counted")


class _Plain:
    """Serves plain requests: scripted results first, then a tool round when asked for one."""

    def __init__(self, tool_rounds: bool = False) -> None:
        self.tool_rounds = tool_rounds
        self.calls       = 0
        self.results: list[dict[str, Any]] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(type="done", stop_reason="end_turn")

    async def send(self, system_prompt: str, messages: Any, tools: Any) -> dict[str, Any]:
        self.calls += 1
        if self.results:
            return self.results.pop(0)
        if self.tool_rounds:
            return {
                "content":    f"round {self.calls}",
                "tool_uses":  [{"id": f"c{self.calls}", "name": "Count", "input": {"n": self.calls}}],
                "stop_reason": "tool_use",
                "usage":      {"input_tokens": 1, "output_tokens": 1},
            }
        return {"content": "done", "tool_uses": [], "stop_reason": "end_turn", "usage": {}}


class _Broken:
    """Fails every plain request."""

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(type="done", stop_reason="end_turn")

    async def send(self, system_prompt: str, messages: Any, tools: Any) -> dict[str, Any]:
        raise RuntimeError("connection reset")


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: Any, **session: Any) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    pricing_path = tmp_path / "pricing.yml"
    pricing_path.write_text(PRICING, encoding="utf-8")
    table         = PricingTable(pricing_path=pricing_path)
    registry      = ToolRegistry()
    registry.register(_Count())
    settings                = NerdvanaSettings()
    settings.cwd            = str(tmp_path)
    settings.model.provider = "acme"
    settings.model.model    = "priced"
    for key, value in session.items():
        setattr(settings.session, key, value)
    return AgentLoop(
        settings         = settings,
        registry         = registry,
        session          = SessionStorage(session_id="resend", storage_dir=str(tmp_path / "sessions")),
        analytics_writer = AnalyticsWriter(db_path=tmp_path / "a.sqlite", pricing_table=table, enabled=False),
        pricing_table    = table,
    )


async def _resend(loop: AgentLoop) -> str:
    """Run the non-streaming resend to its end and return everything it yielded."""
    context = ToolContext(cwd=loop.settings.cwd)
    return "".join([chunk async for chunk in loop.failover.send_without_streaming("system", [], context)])


def _counted(loop: AgentLoop) -> int:
    tool = loop.registry.get("Count")
    assert tool is not None
    return tool.calls


def _limits(tmp_path: Path, model: str = "priced", **session: Any) -> tuple[RunLimits, NerdvanaSettings]:
    pricing = tmp_path / "pricing.yml"
    pricing.write_text(PRICING, encoding="utf-8")
    settings = NerdvanaSettings()
    settings.model.provider, settings.model.model = "acme", model
    for key, value in session.items():
        setattr(settings.session, key, value)
    table         = PricingTable(pricing_path=pricing)
    writer        = AnalyticsWriter(db_path=tmp_path / "a.sqlite", pricing_table=table, enabled=False)
    return RunLimits(settings, Counter(), table, writer), settings


# ---------------------------------------------------------------------------
# Limits checked inside the resend
# ---------------------------------------------------------------------------


async def test_a_spent_token_limit_stops_the_resend_before_the_next_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Plain(tool_rounds=True)
    loop     = _loop(monkeypatch, tmp_path, provider, max_total_tokens=2)
    output   = await _resend(loop)
    assert loop.last_stop == "max_total_tokens"
    assert "Token limit reached" in output
    assert provider.calls == 1        # the first request ran, the second never went out


async def test_a_spent_cost_limit_stops_the_resend_before_the_next_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Plain(tool_rounds=True)
    loop     = _loop(monkeypatch, tmp_path, provider, max_cost_usd=2.0)
    output   = await _resend(loop)
    assert loop.last_stop == "max_cost"
    assert "Cost limit reached" in output
    assert provider.calls == 1
    assert loop.session_cost_usd() == pytest.approx(2.0)


async def test_the_turn_limit_stops_the_resend_once_the_failed_turn_is_accounted_for(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Plain(tool_rounds=True)
    loop     = _loop(monkeypatch, tmp_path, provider, max_turns=3)
    loop.turns_used = 1                # the streaming loop counted the turn whose stream failed
    output   = await _resend(loop)
    assert loop.last_stop == "max_turns"
    assert "Max turns (3) reached" in output
    assert provider.calls == 3         # the resend itself plus the two turns still left
    assert loop.turns_used == 3


async def test_a_failed_request_in_the_resend_ends_the_run_as_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop   = _loop(monkeypatch, tmp_path, _Broken())
    output = await _resend(loop)
    assert loop.last_stop == "provider_error"
    assert "Fallback error" in output
    assert any(m.role == Role.ASSISTANT and m.content == "Error occurred: connection reset" for m in loop.state.messages)


# ---------------------------------------------------------------------------
# Giving up
# ---------------------------------------------------------------------------


async def test_a_resend_that_never_finishes_reports_recovery_exhausted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Plain(tool_rounds=True)
    loop     = _loop(monkeypatch, tmp_path, provider)
    output   = await _resend(loop)
    assert provider.calls == _MAX_RESEND_ROUNDS
    assert loop.last_stop == "recovery_exhausted"
    assert loop.last_stop != "completed"
    assert "Recovery exhausted" in output
    assert any(m.role == Role.ASSISTANT and "Error occurred: recovery exhausted" in m.content for m in loop.state.messages)


# ---------------------------------------------------------------------------
# Tool calls cut short by a length limit
# ---------------------------------------------------------------------------


async def test_a_tool_call_cut_short_by_max_tokens_is_not_run_and_its_text_is_kept(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Plain()
    provider.results = [{
        "content":     "half an answer",
        "tool_uses":   [{"id": "c1", "name": "Count", "input": {}}],
        "stop_reason": "max_tokens",
        "usage":       {"input_tokens": 5},
    }]
    loop   = _loop(monkeypatch, tmp_path, provider)
    output = await _resend(loop)
    assert "half an answer" in output
    assert provider.calls == 1
    assert _counted(loop) == 0
    assert not any(m.role == Role.TOOL for m in loop.state.messages)
    assert any(m.role == Role.ASSISTANT and m.content == "half an answer" for m in loop.state.messages)


async def test_a_finish_reason_of_length_also_holds_the_tool_call_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Plain()
    provider.results = [{
        "content":       "cut",
        "tool_uses":     [{"id": "c1", "name": "Count", "input": {}}],
        "finish_reason": "length",
    }]
    loop   = _loop(monkeypatch, tmp_path, provider)
    output = await _resend(loop)
    assert "cut" in output
    assert _counted(loop) == 0
    assert not any(m.role == Role.TOOL for m in loop.state.messages)


async def test_a_complete_tool_call_still_runs_in_the_resend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Plain()
    provider.results = [{
        "content":     "all there",
        "tool_uses":   [{"id": "c1", "name": "Count", "input": {}}],
        "stop_reason": "tool_use",
    }]
    loop = _loop(monkeypatch, tmp_path, provider)
    await _resend(loop)
    assert provider.calls == 2            # the tool round, then the plain answer that follows it
    assert _counted(loop) == 1


# ---------------------------------------------------------------------------
# The unpriced model after a fallback
# ---------------------------------------------------------------------------


def test_require_price_refuses_every_call_once_a_fallback_reaches_an_unpriced_model(tmp_path: Path) -> None:
    limits, settings = _limits(tmp_path, model="priced", max_cost_usd=1.0, require_price=True)
    assert limits.unpriced() == ("", "")           # the model in use has a price
    settings.model.model = "unknown"               # the fallback landed on a model without one
    first  = limits.unpriced()
    second = limits.unpriced()
    assert first[0] == "unpriced" and "Refusing to run" in first[1]
    assert second == first                         # not gated by a once-flag: every request is refused
    settings.model.model = "priced"
    assert limits.unpriced() == ("", "")           # back to a model that can be priced


def test_the_no_price_warning_is_per_model_and_never_gates_a_later_refusal(tmp_path: Path) -> None:
    limits, settings = _limits(tmp_path, model="unknown", max_cost_usd=1.0)
    stop, notice = limits.unpriced()
    assert stop == "" and "is not enforced" in notice
    assert limits.unpriced() == ("", "")           # the same model is warned about once
    settings.model.model = "other_unknown"
    stop, notice = limits.unpriced()
    assert stop == "" and "is not enforced" in notice and "other_unknown" in notice
    settings.session.require_price = True          # the refusal is asked for after the warning already fired
    assert limits.unpriced()[0] == "unpriced"
    assert limits.unpriced()[0] == "unpriced"


def test_a_priced_model_says_nothing_even_when_a_limit_is_set(tmp_path: Path) -> None:
    limits, settings = _limits(tmp_path, model="priced", max_cost_usd=1.0, require_price=True)
    assert limits.unpriced() == ("", "")
    assert limits.unpriced() == ("", "")


def test_without_a_cost_limit_an_unpriced_model_is_never_refused(tmp_path: Path) -> None:
    limits, _ = _limits(tmp_path, model="unknown", require_price=True)
    assert limits.unpriced() == ("", "")
