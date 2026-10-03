"""Tool call ids stay unique in the history the provider receives.

Providers reject a request that holds two tool calls with the same id
("duplicate tool_call id"), and a history that already holds one fails every
later request of the session.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.loop.agent_loop import AgentLoop
from nerdvana_cli.core.loop.tool_ids import collect_tool_use_ids, new_tool_use_id, repair_tool_ids
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.tool import BaseTool, ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import Message, Role, ToolResult

# ---------------------------------------------------------------------------
# repair_tool_ids
# ---------------------------------------------------------------------------


def _assistant(*ids: str) -> Message:
    return Message(role=Role.ASSISTANT, content="", tool_uses=[{"id": i, "name": "Echo", "input": {"n": n}} for n, i in enumerate(ids)])


def _result(tool_use_id: str, content: str = "ok") -> Message:
    return Message(role=Role.TOOL, content=content, tool_use_id=tool_use_id)


def _pairs_are_consistent(messages: list[Message]) -> bool:
    """Every result names an id requested by the assistant message before it."""
    requested: set[str] = set()
    for message in messages:
        if message.role == Role.ASSISTANT:
            requested = {tu["id"] for tu in message.tool_uses}
        elif message.role == Role.TOOL and message.tool_use_id not in requested:
            return False
    return True


def test_healthy_history_is_left_alone() -> None:
    messages = [_assistant("a", "b"), _result("a"), _result("b"), _assistant("c"), _result("c")]
    assert repair_tool_ids(messages) == 0
    assert collect_tool_use_ids(messages) == {"a", "b", "c"}


def test_an_id_reused_by_a_later_turn_is_renumbered_with_its_result() -> None:
    messages = [_assistant("x"), _result("x", "first"), _assistant("x"), _result("x", "second")]
    assert repair_tool_ids(messages) == 1
    first, second = messages[0].tool_uses[0]["id"], messages[2].tool_uses[0]["id"]
    assert first == "x"
    assert second != "x"
    assert [m.tool_use_id for m in messages if m.role == Role.TOOL] == [first, second]
    assert _pairs_are_consistent(messages)


def test_duplicate_ids_inside_one_turn_pair_results_in_order() -> None:
    messages = [_assistant("x", "x"), _result("x", "one"), _result("x", "two")]
    assert repair_tool_ids(messages) == 1
    first, second = (tu["id"] for tu in messages[0].tool_uses)
    assert first != second
    assert [(m.tool_use_id, m.content) for m in messages[1:]] == [(first, "one"), (second, "two")]


def test_empty_ids_are_replaced() -> None:
    messages = [_assistant(""), _result("")]
    assert repair_tool_ids(messages) == 1
    assert messages[0].tool_uses[0]["id"]
    assert messages[1].tool_use_id == messages[0].tool_uses[0]["id"]


def test_repair_is_idempotent() -> None:
    messages = [_assistant("x"), _result("x"), _assistant("x", "x"), _result("x"), _result("x")]
    assert repair_tool_ids(messages) == 2
    assert repair_tool_ids(messages) == 0
    ids = [tu["id"] for m in messages if m.role == Role.ASSISTANT for tu in m.tool_uses]
    assert len(ids) == len(set(ids))
    assert _pairs_are_consistent(messages)


def test_generated_ids_fit_the_shortest_provider_limit_and_avoid_taken_ones() -> None:
    long_name = "find_referencing_symbols_in_a_very_long_tool_name_indeed"
    assert len(new_tool_use_id(long_name)) <= 40
    assert new_tool_use_id("Echo", {"call_Echo_00000000"}) != "call_Echo_00000000"


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


class _Echo(BaseTool[Any]):
    name             = "Echo"
    description_text = "echo"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        self.calls.append(dict(args))
        return ToolResult(tool_use_id="", content=f"echo {len(self.calls)}")


def _use(call_id: str, n: int) -> ProviderEvent:
    return ProviderEvent(type="tool_use_complete", tool_use_id=call_id, tool_name="Echo", tool_input_complete={"n": n})


def _done(reason: str) -> ProviderEvent:
    return ProviderEvent(type="done", stop_reason=reason)


class _Scripted:
    """Serves one scripted event list per request and keeps the payloads it saw."""

    def __init__(self, responses: list[list[ProviderEvent]]) -> None:
        self.responses = list(responses)
        self.payloads: list[list[dict[str, Any]]] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.payloads.append([dict(m) for m in messages])
        script = self.responses.pop(0) if self.responses else [_done("end_turn")]
        for event in script:
            yield event


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: _Scripted, tool: _Echo) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    registry = ToolRegistry()
    registry.register(tool)
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    return AgentLoop(
        settings = settings,
        registry = registry,
        session  = SessionStorage(session_id="ids", storage_dir=str(tmp_path / "sessions")),
    )


def _history_ids(loop: AgentLoop) -> list[str]:
    return [tu["id"] for m in loop.state.messages if m.role == Role.ASSISTANT for tu in m.tool_uses]


def _payload_ids(payload: list[dict[str, Any]]) -> list[str]:
    return [tu["id"] for m in payload if m.get("role") == "assistant" for tu in m.get("tool_uses", [])]


async def test_a_response_that_repeats_its_final_events_runs_the_call_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    tool     = _Echo()
    provider = _Scripted([
        [_use("call_a", 1), _done("tool_use"), _use("call_a", 1), _done("tool_use")],
        [_done("end_turn")],
    ])
    loop = _loop(monkeypatch, tmp_path, provider, tool)

    async for _ in loop.run("go"):
        pass

    assert len(tool.calls) == 1
    assert _history_ids(loop) == ["call_a"]
    assert [m.tool_use_id for m in loop.state.messages if m.role == Role.TOOL] == ["call_a"]


async def test_an_echoed_call_inside_one_response_is_dropped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tool     = _Echo()
    provider = _Scripted([[_use("call_a", 1), _use("call_a", 1), _use("call_b", 2), _done("tool_use")], [_done("end_turn")]])
    loop     = _loop(monkeypatch, tmp_path, provider, tool)

    async for _ in loop.run("go"):
        pass

    assert [c["n"] for c in tool.calls] == [1, 2]
    assert _history_ids(loop) == ["call_a", "call_b"]


async def test_the_same_id_on_two_different_calls_is_renumbered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tool     = _Echo()
    provider = _Scripted([[_use("call_a", 1), _use("call_a", 2), _done("tool_use")], [_done("end_turn")]])
    loop     = _loop(monkeypatch, tmp_path, provider, tool)

    async for _ in loop.run("go"):
        pass

    ids = _history_ids(loop)
    assert len(ids) == 2
    assert len(set(ids)) == 2
    assert _pairs_are_consistent(loop.state.messages)
    assert [c["n"] for c in tool.calls] == [1, 2]


async def test_an_id_the_provider_reuses_on_a_later_turn_never_reaches_it_twice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    tool     = _Echo()
    provider = _Scripted([
        [_use("call_x", 1), _done("tool_use")],
        [_use("call_x", 2), _done("tool_use")],
        [_done("end_turn")],
    ])
    loop = _loop(monkeypatch, tmp_path, provider, tool)

    async for _ in loop.run("go"):
        pass

    assert len(provider.payloads) == 3
    for payload in provider.payloads:
        ids = _payload_ids(payload)
        assert len(ids) == len(set(ids))
    assert len(_history_ids(loop)) == 2
    assert _pairs_are_consistent(loop.state.messages)


async def test_a_history_that_already_holds_duplicates_is_repaired_before_the_next_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    tool     = _Echo()
    provider = _Scripted([[_done("end_turn")]])
    loop     = _loop(monkeypatch, tmp_path, provider, tool)
    loop.state.messages.extend([
        Message(role=Role.USER, content="earlier"),
        _assistant("call_dup"),
        _result("call_dup", "one"),
        _assistant("call_dup"),
        _result("call_dup", "two"),
    ])

    async for _ in loop.run("continue"):
        pass

    ids = _payload_ids(provider.payloads[0])
    assert len(ids) == 2
    assert len(set(ids)) == 2
    assert _pairs_are_consistent(loop.state.messages)
