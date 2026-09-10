"""Wiring the agent loop actually performs at runtime, not only in tests.

Three mechanisms are checked here: analytics that record rows while a real turn
runs, naive compaction that never hands a provider a widowed tool result, and
BEFORE_API_CALL reaching handlers before the request leaves. Costs are asserted
against an injected synthetic price table, never against the shipped one.

Author: 최진호
Date:   2026-09-11
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.analytics import AnalyticsWriter, PricingTable
from nerdvana_cli.core.hooks import HookContext, HookEvent, HookResult
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import ModelConfig, NerdvanaSettings, SessionConfig
from nerdvana_cli.core.tool import BaseTool, ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import Message, PermissionBehavior, PermissionResult, Role, ToolResult

# Rates chosen so a per-1k reading of the table would land three orders of
# magnitude above the window the cost test accepts.
_SYNTHETIC_PRICING = """
anthropic:
  wiring-test-model:
    input_per_1m: 3.0
    output_per_1m: 15.0
"""

_SESSION_ID = "wiring-146"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _EchoTool(BaseTool[Any]):
    name                = "Echo"
    description_text    = "Echo its input back"
    is_concurrency_safe = False

    def parse_args(self, raw: dict[str, Any]) -> dict[str, Any]:
        return raw

    async def call(
        self,
        args:         Any,
        context:      Any,
        can_use_tool: Any = None,
        on_progress:  Any = None,
    ) -> ToolResult:
        return ToolResult(tool_use_id="", content=f"echo:{args.get('text', '')}")

    def check_permissions(self, args: Any, context: Any) -> PermissionResult:
        return PermissionResult(behavior=PermissionBehavior.ALLOW)

    def validate_input(self, args: Any, context: Any) -> str | None:
        return None


class _RecordingProvider:
    """Serves one event sequence per call and remembers the payloads it saw."""

    def __init__(self, sequences: list[list[ProviderEvent]]) -> None:
        self._sequences = sequences
        self._calls     = 0
        self.payloads: list[list[dict[str, Any]]] = []

    async def stream(
        self,
        system_prompt: str,
        messages:      list[dict[str, Any]],
        tools:         list[Any],
    ) -> Any:
        self.payloads.append(list(messages))
        index        = min(self._calls, len(self._sequences) - 1)
        self._calls += 1
        for event in self._sequences[index]:
            yield event


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def pricing(tmp_path: Path) -> PricingTable:
    """Price table under this test's control, decoupled from the shipped one."""
    path = tmp_path / "pricing.yml"
    path.write_text(_SYNTHETIC_PRICING, encoding="utf-8")
    return PricingTable(pricing_path=path)


def _settings(cwd: str) -> NerdvanaSettings:
    settings         = NerdvanaSettings()
    settings.model   = ModelConfig(provider="anthropic", model="wiring-test-model", api_key="test-key")
    settings.session = SessionConfig(persist=False, max_turns=5, max_context_tokens=1000)
    settings.cwd     = cwd
    settings.verbose = False
    return settings


def _build_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path:    Path,
    provider:    Any,
    writer:      AnalyticsWriter,
    pricing:     PricingTable,
    tools:       list[BaseTool[Any]] | None = None,
) -> AgentLoop:
    """AgentLoop with every outbound edge replaced by a local double."""
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")

    registry = ToolRegistry()
    for tool in tools or []:
        registry.register(tool)

    return AgentLoop(
        settings         = _settings(str(tmp_path)),
        registry         = registry,
        session          = SessionStorage(session_id=_SESSION_ID, storage_dir=str(tmp_path / "sessions")),
        analytics_writer = writer,
        pricing_table    = pricing,
    )


def _writer(tmp_path: Path, pricing: PricingTable) -> AnalyticsWriter:
    return AnalyticsWriter(db_path=tmp_path / "analytics.sqlite", pricing_table=pricing, enabled=True)


async def _drain(loop: AgentLoop, prompt: str = "go") -> list[str]:
    return [chunk async for chunk in loop.run(prompt)]


def _tool_turn(text: str = "hi") -> list[list[ProviderEvent]]:
    """One tool round trip, then a plain answer with reported usage."""
    return [
        [
            ProviderEvent(
                type                = "tool_use_complete",
                tool_use_id         = "call_Echo_0000abcd",
                tool_name           = "Echo",
                tool_input_complete = {"text": text},
            ),
            ProviderEvent(type="done", stop_reason="tool_use"),
        ],
        [
            ProviderEvent(type="content_delta", content="done"),
            ProviderEvent(type="usage", usage={"input_tokens": 20_000, "output_tokens": 5_000}),
            ProviderEvent(type="done", stop_reason="end_turn"),
        ],
    ]


def _orphan_prone_history() -> list[Message]:
    """History whose naive truncation window opens on a tool result."""
    filler = "x" * 400
    return [
        Message(role=Role.USER,      content=filler),
        Message(role=Role.ASSISTANT, content=filler),
        Message(role=Role.USER,      content=filler),
        Message(role=Role.ASSISTANT, content=filler),
        Message(role=Role.TOOL,      content=filler, tool_use_id="call_Echo_0000abcd"),
        Message(role=Role.ASSISTANT, content=filler),
        Message(role=Role.USER,      content=filler),
        Message(role=Role.ASSISTANT, content=filler),
    ]


def _orphans(messages: list[Message]) -> list[str]:
    """tool_use_ids of results left without the tool_use that produced them."""
    known: set[str] = set()
    loose: list[str] = []
    for msg in messages:
        if msg.role == Role.ASSISTANT and msg.tool_uses:
            known.update(str(tu.get("id", "")) for tu in msg.tool_uses)
        elif msg.role == Role.TOOL and str(msg.tool_use_id or "") not in known:
            loose.append(str(msg.tool_use_id or ""))
    return loose


# ---------------------------------------------------------------------------
# Analytics reach the database from a real turn
# ---------------------------------------------------------------------------

async def test_running_a_tool_writes_rows_to_the_analytics_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path:    Path,
    pricing:     PricingTable,
) -> None:
    """A turn that calls a tool must leave a row the dashboard can read."""
    db     = tmp_path / "analytics.sqlite"
    writer = _writer(tmp_path, pricing)
    loop   = _build_loop(monkeypatch, tmp_path, _RecordingProvider(_tool_turn()), writer, pricing, [_EchoTool()])

    await _drain(loop)

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT tool_name, success FROM tool_calls WHERE session_id = ?",
            (_SESSION_ID,),
        ).fetchall()
        started = conn.execute(
            "SELECT id FROM sessions WHERE id = ?", (_SESSION_ID,),
        ).fetchone()

    assert rows == [("Echo", 1)], "the executed tool left no analytics row"
    assert started is not None, "the loop never opened a session row to hang the calls on"


async def test_recorded_session_cost_has_a_realistic_magnitude(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path:    Path,
    pricing:     PricingTable,
) -> None:
    """25k tokens at the injected rates cost cents, not dollars or millidollars.

    The window is wide on purpose: it catches a per-1k reading of a per-1m
    table (1000x high) and a table that never loaded (0.0), without pinning a
    figure that a price change would falsify.
    """
    db     = tmp_path / "analytics.sqlite"
    writer = _writer(tmp_path, pricing)
    loop   = _build_loop(monkeypatch, tmp_path, _RecordingProvider(_tool_turn()), writer, pricing, [_EchoTool()])

    await _drain(loop)

    with sqlite3.connect(db) as conn:
        tokens, spend = conn.execute(
            "SELECT token_total, cost_total FROM sessions WHERE id = ?",
            (_SESSION_ID,),
        ).fetchone()

    assert tokens == 25_000, "reported usage never reached the session row"
    assert 0.01 < spend < 1.0, f"session cost {spend} is off by orders of magnitude"


async def test_each_tool_call_row_carries_a_priced_provider_and_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path:    Path,
    pricing:     PricingTable,
) -> None:
    """`nerdvana cost` reads tool_calls, so a row without a price is invisible.

    A short echo is a handful of tokens at the injected rates, so the amount
    belongs well under a cent. The upper bound is what a per-1k reading of a
    per-1m table would break.
    """
    db     = tmp_path / "analytics.sqlite"
    writer = _writer(tmp_path, pricing)
    loop   = _build_loop(monkeypatch, tmp_path, _RecordingProvider(_tool_turn()), writer, pricing, [_EchoTool()])

    await _drain(loop)

    with sqlite3.connect(db) as conn:
        provider, model, sent, received, spend = conn.execute(
            """SELECT provider, model, input_tokens, output_tokens, cost_usd
               FROM tool_calls WHERE session_id = ?""",
            (_SESSION_ID,),
        ).fetchone()

    assert (provider, model) == ("anthropic", "wiring-test-model"), (
        "the row cannot be priced without the provider and model it ran under"
    )
    assert sent > 0 and received > 0, "neither side of the exchange was counted"
    assert 0.0 < spend < 0.01, f"tool call cost {spend} is off by orders of magnitude"


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------

async def test_naive_compaction_leaves_no_orphan_tool_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path:    Path,
    pricing:     PricingTable,
) -> None:
    """With the circuit open the loop truncates, and must still drop widows."""
    writer = _writer(tmp_path, pricing)
    loop   = _build_loop(monkeypatch, tmp_path, _RecordingProvider([[]]), writer, pricing)

    loop.state.messages = _orphan_prone_history()
    loop._compaction_state.consecutive_failures = loop._compaction_state.max_failures
    assert loop._compaction_state.is_circuit_open, "the naive branch was not the one under test"

    statuses = [status async for status in loop._maybe_compact_messages(800, 200)]

    assert statuses == [], "the naive path must not advertise AI compression"
    assert loop.state.messages, "compaction emptied the history"
    assert loop.state.messages[0].role != Role.TOOL, (
        "the first message is a tool result whose tool_use is gone; the provider answers 400"
    )
    assert _orphans(loop.state.messages) == []


async def test_ai_compaction_still_replaces_history_with_a_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path:    Path,
    pricing:     PricingTable,
) -> None:
    """The untouched AI branch keeps summarising and trimming leading results."""
    summary = Message(role=Role.USER, content="[summary]")

    async def _fake_ai_compact(messages: Any, provider: Any, state: Any, *, prompt: str) -> Message:
        return summary

    monkeypatch.setattr("nerdvana_cli.core.agent_loop.ai_compact", _fake_ai_compact)

    writer = _writer(tmp_path, pricing)
    loop   = _build_loop(monkeypatch, tmp_path, _RecordingProvider([[]]), writer, pricing)
    loop.state.messages = _orphan_prone_history()

    statuses = [status async for status in loop._maybe_compact_messages(800, 200)]

    assert statuses[0].startswith("\x00COMPACT:")
    assert statuses[-1].endswith("done")
    assert loop.state.messages[0] is summary
    assert len(loop.state.messages) == 4, "summary plus the trimmed recent window"
    assert _orphans(loop.state.messages) == []


# ---------------------------------------------------------------------------
# BEFORE_API_CALL
# ---------------------------------------------------------------------------

async def test_before_api_call_fires_ahead_of_the_provider_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path:    Path,
    pricing:     PricingTable,
) -> None:
    """The documented extension point runs, and its injection is sent."""
    provider = _RecordingProvider([
        [
            ProviderEvent(type="content_delta", content="ok"),
            ProviderEvent(type="done", stop_reason="end_turn"),
        ],
    ])
    writer = _writer(tmp_path, pricing)
    loop   = _build_loop(monkeypatch, tmp_path, provider, writer, pricing)

    seen: list[HookContext] = []

    def _handler(ctx: HookContext) -> HookResult:
        seen.append(ctx)
        return HookResult(inject_messages=[{"role": "user", "content": "[injected]"}])

    loop.hooks.register(HookEvent.BEFORE_API_CALL, _handler)

    await _drain(loop)

    assert len(seen) == 1, "BEFORE_API_CALL never fired on the streaming path"
    assert seen[0].event == HookEvent.BEFORE_API_CALL
    assert provider.payloads, "the provider was never called"
    assert any(
        entry.get("content") == "[injected]" for entry in provider.payloads[0]
    ), "the hook injected a message that never reached the request it preceded"


async def test_before_api_call_fires_once_per_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path:    Path,
    pricing:     PricingTable,
) -> None:
    """A tool round trip means two requests, so the hook runs twice."""
    provider = _RecordingProvider(_tool_turn())
    writer   = _writer(tmp_path, pricing)
    loop     = _build_loop(monkeypatch, tmp_path, provider, writer, pricing, [_EchoTool()])

    fired: list[str] = []
    loop.hooks.register(HookEvent.BEFORE_API_CALL, lambda ctx: fired.append(ctx.event) or HookResult())

    await _drain(loop)

    assert len(fired) == len(provider.payloads) == 2


# ---------------------------------------------------------------------------
# Regression guard for the paths that were not meant to change
# ---------------------------------------------------------------------------

async def test_tool_round_trip_still_produces_the_same_transcript(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path:    Path,
    pricing:     PricingTable,
) -> None:
    """Tool result, ordering and final text survive the new wiring."""
    writer = _writer(tmp_path, pricing)
    loop   = _build_loop(monkeypatch, tmp_path, _RecordingProvider(_tool_turn()), writer, pricing, [_EchoTool()])

    chunks = await _drain(loop)

    assert "done" in chunks
    roles = [msg.role for msg in loop.state.messages]
    assert Role.TOOL in roles
    assert _orphans(loop.state.messages) == []
    tool_messages = [msg for msg in loop.state.messages if msg.role == Role.TOOL]
    assert tool_messages[0].content == "echo:hi"
    assert not tool_messages[0].is_error
