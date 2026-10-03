"""Compacting on the provider's side: when the loop asks, what it keeps, and the fallback to its own compaction.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.agent_loop import COMPACT_STATUS_PREFIX, AgentLoop
from nerdvana_cli.core.compaction_block import carries_compaction, last_compaction_index
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.context_budget import ContextBudget
from nerdvana_cli.core.loop_state import LoopFlow, LoopTurn
from nerdvana_cli.core.provider_recovery import ProviderCallError, RecoveryPlanner
from nerdvana_cli.core.session import SessionStorage, messages_from_transcript
from nerdvana_cli.core.telemetry.analytics import AnalyticsWriter, PricingTable
from nerdvana_cli.core.tool import BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.providers.errors import CONTEXT_LIMIT, ProviderFailure
from nerdvana_cli.types import Message, Role, ToolResult

BLOCK = {"type": "compaction", "content": "Summary: the user builds a recipe app.", "signature": "sig-1"}
OK    = {"content": "", "tool_uses": [], "stop_reason": "compaction", "usage": {"input_tokens": 5000, "output_tokens": 300}, "provider_blocks": [BLOCK]}

PRICING = "acme:\n  priced: {input_per_1m: 1000000.0, output_per_1m: 1000000.0}\n"


class _Provider:
    """A provider that can compact: ``result`` is what ``compact`` answers (an exception is raised)."""

    supports_server_compaction = True

    def __init__(self, result: Any = None, stream_input: int = 700) -> None:
        self.result       = result if result is not None else OK
        self.stream_input = stream_input
        self.compact_calls: list[tuple[str, list[dict[str, Any]], list[str]]] = []
        self.payloads:      list[list[dict[str, Any]]] = []

    async def compact(self, system_prompt: str, messages: list[dict[str, Any]], tools: Any) -> dict[str, Any]:
        self.compact_calls.append((system_prompt, [dict(m) for m in messages], [t.name for t in tools]))
        if isinstance(self.result, Exception):
            raise self.result
        return dict(self.result)

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.payloads.append([dict(m) for m in messages])
        yield ProviderEvent(type="content_delta", content="ok")
        yield ProviderEvent(type="usage", usage={"input_tokens": self.stream_input, "output_tokens": 5})
        yield ProviderEvent(type="done", stop_reason="end_turn")


class _Tool(BaseTool[Any]):
    name             = "Plain"
    description_text = "plain"

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="plain")


def _history() -> list[Message]:
    return [Message(role=Role.USER, content="a " * 1500), Message(role=Role.ASSISTANT, content="b " * 1500)]


def _loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: Any, compaction: str = "on", max_failures: int = 3,
) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    pricing = tmp_path / "pricing.yml"
    pricing.write_text(PRICING, encoding="utf-8")
    table    = PricingTable(pricing_path=pricing)
    registry = ToolRegistry()
    registry.register(_Tool())
    settings                            = NerdvanaSettings()
    settings.cwd                        = str(tmp_path)
    settings.model.provider             = "acme"
    settings.model.model                = "priced"
    settings.model.anthropic_compaction = compaction  # type: ignore[assignment]
    settings.session.max_context_tokens = 1000
    settings.session.compact_max_failures = max_failures
    loop = AgentLoop(
        settings         = settings,
        registry         = registry,
        session          = SessionStorage(session_id="sc", storage_dir=str(tmp_path / "s")),
        analytics_writer = AnalyticsWriter(db_path=tmp_path / "a.sqlite", pricing_table=table, enabled=True),
        pricing_table    = table,
    )
    loop.state.messages = _history()
    return loop


async def _drain(loop: AgentLoop, prompt: str = "next") -> list[str]:
    return [chunk async for chunk in loop.run(prompt)]


def _fallback_recorder(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    calls: list[tuple[int, int]] = []

    async def _compact(self: AgentLoop, cur_toks: int, thr: int) -> AsyncIterator[str]:
        calls.append((cur_toks, thr))
        if False:
            yield ""

    monkeypatch.setattr(AgentLoop, "_maybe_compact_messages", _compact)
    return calls


# ---------------------------------------------------------------------------
# On success
# ---------------------------------------------------------------------------


async def test_the_loop_asks_the_provider_with_the_turns_system_prompt_history_and_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Provider()
    loop     = _loop(monkeypatch, tmp_path, provider)
    await _drain(loop)
    assert len(provider.compact_calls) == 1
    system, messages, tools = provider.compact_calls[0]
    assert system.startswith("system")
    assert [m["role"] for m in messages[:2]] == ["user", "assistant"] and messages[-1]["content"] == "next"
    assert tools == ["Plain"]


async def test_the_block_is_appended_as_an_assistant_message_and_the_history_stays_whole(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Provider()
    loop     = _loop(monkeypatch, tmp_path, provider)
    output   = await _drain(loop)
    roles    = [m.role for m in loop.state.messages]
    assert roles[:3] == [Role.USER, Role.ASSISTANT, Role.USER]          # nothing before the block was removed
    block_at = last_compaction_index(loop.state.messages)
    block    = loop.state.messages[block_at]
    assert block.role == Role.ASSISTANT and block.content == "" and block.provider_blocks == [BLOCK]
    assert loop.state.messages[block_at + 1].role == Role.USER          # the next request ends on a user message
    assert provider.payloads[0][block_at]["provider_blocks"] == [BLOCK]
    assert f"{COMPACT_STATUS_PREFIX}done" in output
    assert any(chunk.startswith(f"{COMPACT_STATUS_PREFIX}compressing") for chunk in output)


async def test_the_block_is_recorded_in_the_session_and_restored_with_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop = _loop(monkeypatch, tmp_path, _Provider())
    await _drain(loop)
    entries = loop.session.replay()
    assert any(e["type"] == "assistant" and e.get("provider_blocks") == [BLOCK] and e["content"] == "" for e in entries)
    assert [e["strategy"] for e in entries if e["type"] == "compaction"] == ["server"]
    restored = loop.session.load_messages()
    assert any(m.provider_blocks == [BLOCK] for m in restored)


async def test_the_summarizing_call_is_counted_in_the_totals_the_ledger_and_the_cost(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop = _loop(monkeypatch, tmp_path, _Provider())
    await _drain(loop)
    assert loop.usage_summary()["input_tokens"] == 5000 + 700
    assert loop.usage_summary()["output_tokens"] == 300 + 5
    assert loop.session_cost_usd() == pytest.approx(5300.0 + 705.0)
    conn = sqlite3.connect(tmp_path / "a.sqlite")
    try:
        rows = conn.execute("SELECT provider, model, input_tokens, output_tokens FROM api_calls ORDER BY id").fetchall()
    finally:
        conn.close()
    assert rows == [("acme", "priced", 5000, 300), ("acme", "priced", 700, 5)]
    assert loop.signal_summary()["compaction"] == 1


async def test_the_cache_watch_does_not_take_the_new_start_for_a_miss(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop = _loop(monkeypatch, tmp_path, _Provider())
    await _drain(loop)
    assert "cache_miss" not in loop.signal_summary()


async def test_prompt_marks_stay_because_the_history_is_not_rewritten(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop = _loop(monkeypatch, tmp_path, _Provider())
    await _drain(loop)
    assert loop.rewinder.marks != []


async def test_nothing_is_asked_below_the_threshold(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Provider()
    loop     = _loop(monkeypatch, tmp_path, provider)
    loop.state.messages = []
    await _drain(loop)
    assert provider.compact_calls == []


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("result", [
    {"content": "boom", "is_error": True},
    {"content": "", "tool_uses": [], "stop_reason": "max_tokens", "usage": {}},
    RuntimeError("network down"),
])
async def test_an_error_or_a_missing_summary_falls_back_to_the_client_side_compaction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, result: Any,
) -> None:
    fallback = _fallback_recorder(monkeypatch)
    provider = _Provider(result)
    loop     = _loop(monkeypatch, tmp_path, provider)
    await _drain(loop)
    assert len(provider.compact_calls) == 1
    assert len(fallback) == 1
    assert last_compaction_index(loop.state.messages) == 0 and not carries_compaction(loop.state.messages[0].provider_blocks)
    assert loop.signal_summary().get("compaction", 0) == 0     # the fallback is stubbed here; the request failure counts nothing


async def test_the_provider_is_not_asked_again_after_too_many_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fallback = _fallback_recorder(monkeypatch)
    provider = _Provider({"content": "no", "is_error": True}, stream_input=900)
    loop     = _loop(monkeypatch, tmp_path, provider, max_failures=2)
    for _ in range(4):
        await _drain(loop)
    assert len(provider.compact_calls) == 2
    assert len(fallback) == 4


async def test_a_success_resets_the_failure_count(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fallback_recorder(monkeypatch)
    provider = _Provider({"content": "no", "is_error": True}, stream_input=900)
    loop     = _loop(monkeypatch, tmp_path, provider, max_failures=2)
    await _drain(loop)
    provider.result = OK
    await _drain(loop)
    provider.result = {"content": "no", "is_error": True}
    await _drain(loop)
    await _drain(loop)
    assert len(provider.compact_calls) == 4   # the success between the failures kept the breaker from opening


@pytest.mark.parametrize("compaction", ["off"])
async def test_with_the_setting_off_the_provider_is_not_asked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, compaction: str) -> None:
    fallback = _fallback_recorder(monkeypatch)
    provider = _Provider()
    loop     = _loop(monkeypatch, tmp_path, provider, compaction=compaction)
    await _drain(loop)
    assert provider.compact_calls == [] and len(fallback) == 1


async def test_a_provider_that_cannot_compact_is_not_asked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fallback = _fallback_recorder(monkeypatch)
    provider = _Provider()
    provider.supports_server_compaction = False
    loop     = _loop(monkeypatch, tmp_path, provider)
    await _drain(loop)
    assert provider.compact_calls == [] and len(fallback) == 1


async def test_a_context_limit_failure_in_the_middle_of_a_request_uses_the_client_side_compaction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    fallback = _fallback_recorder(monkeypatch)
    provider = _Provider()
    loop     = _loop(monkeypatch, tmp_path, provider)
    exc      = ProviderCallError("too long", ProviderFailure(CONTEXT_LIMIT, 400, None))
    flow     = LoopFlow(context_tokens=2000)
    turn     = LoopTurn(messages=[], used_ids=set(), sent_count=0)
    _ = [c async for c in loop.failover.recover(exc, RecoveryPlanner(fallbacks=[]), turn, flow, "system", [], ToolContext())]
    assert provider.compact_calls == [] and len(fallback) == 1


# ---------------------------------------------------------------------------
# The context estimate and the transcript
# ---------------------------------------------------------------------------


def _msg(text: str, blocks: list[dict[str, Any]] | None = None, role: Role = Role.USER) -> Message:
    return Message(role=role, content=text, provider_blocks=blocks or [])


def test_without_a_measurement_only_the_messages_from_the_last_block_on_are_estimated() -> None:
    budget   = ContextBudget()
    history  = [_msg("x " * 5000), _msg("y " * 5000, role=Role.ASSISTANT)]
    before   = budget.current(history)
    compact  = [*history, _msg("", [BLOCK], Role.ASSISTANT), _msg("next")]
    after    = budget.current(compact)
    assert before > 4000 and after < 100


def test_a_measurement_taken_before_the_block_is_not_used_after_it() -> None:
    budget = ContextBudget()
    budget.record_usage(90_000, messages_sent=2)
    history = [_msg("x"), _msg("y", role=Role.ASSISTANT), _msg("", [BLOCK], Role.ASSISTANT), _msg("next")]
    assert budget.current(history) < 100


def test_a_measurement_of_the_request_that_carried_the_block_is_used() -> None:
    budget = ContextBudget()
    history = [_msg("x"), _msg("", [BLOCK], Role.ASSISTANT), _msg("next")]
    budget.record_usage(1234, messages_sent=len(history))
    history.append(_msg("z" * 40))
    assert budget.current(history) == 1234 + 10


def test_a_history_without_a_block_is_estimated_as_before() -> None:
    budget = ContextBudget()
    budget.record_usage(50_000, messages_sent=1)
    assert budget.current([_msg("a"), _msg("b" * 40)]) == 50_000 + 10


def test_an_assistant_entry_that_holds_only_a_compaction_block_is_restored_and_other_empty_ones_are_not() -> None:
    entries = [
        {"type": "user", "content": "hi"},
        {"type": "assistant", "content": "", "tool_uses": [], "provider_blocks": [BLOCK]},
        {"type": "assistant", "content": "", "tool_uses": [], "provider_blocks": [{"type": "thinking", "thinking": "t"}]},
        {"type": "assistant", "content": "", "tool_uses": []},
    ]
    messages = messages_from_transcript(entries)
    assert [m.role for m in messages] == [Role.USER, Role.ASSISTANT]
    assert messages[1].provider_blocks == [BLOCK]


def test_the_block_predicate() -> None:
    assert carries_compaction([BLOCK]) and not carries_compaction([{"type": "thinking"}]) and not carries_compaction([])
    assert last_compaction_index([_msg("a")]) == 0
    assert last_compaction_index([_msg("a"), _msg("", [BLOCK], Role.ASSISTANT), _msg("b")]) == 1
