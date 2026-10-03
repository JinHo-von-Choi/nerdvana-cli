"""Session lifecycle: transcript restore, persistence switch, stream limits.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.state.session import SessionStorage, messages_from_transcript, resume_session_id
from nerdvana_cli.core.stream_guard import StreamTimeoutError, guarded_stream
from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import Role

# ---------------------------------------------------------------------------
# Transcript restore
# ---------------------------------------------------------------------------


def test_restore_keeps_complete_tool_pairs_in_order() -> None:
    entries = [
        {"type": "user", "content": "fix it"},
        {"type": "assistant", "content": "", "tool_uses": [{"id": "t1", "name": "FileRead", "input": {}}]},
        {"type": "tool_result", "tool_use_id": "t1", "content": "body", "is_error": False},
        {"type": "assistant", "content": "done", "tool_uses": []},
    ]
    messages = messages_from_transcript(entries)
    assert [m.role for m in messages] == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
    assert messages[2].tool_use_id == "t1"


def test_restore_drops_unanswered_tool_use_and_widowed_result() -> None:
    entries = [
        {"type": "user", "content": "go"},
        {"type": "tool_result", "tool_use_id": "ghost", "content": "x"},
        {"type": "assistant", "content": "", "tool_uses": [{"id": "t9", "name": "Bash", "input": {}}]},
    ]
    messages = messages_from_transcript(entries)
    assert [m.role for m in messages] == [Role.USER]


def test_restore_marks_results_cut_by_the_transcript_cap() -> None:
    entries = [
        {"type": "assistant", "content": "", "tool_uses": [{"id": "t1", "name": "FileRead", "input": {}}]},
        {"type": "tool_result", "tool_use_id": "t1", "content": "y" * 500},
    ]
    assert "truncated" in str(messages_from_transcript(entries)[1].content)


def test_restore_brings_back_provider_blocks_and_a_tool_only_turn_survives_the_round_trip(tmp_path: Path) -> None:
    block   = {"type": "thinking", "thinking": "plan", "signature": "sig"}
    storage = SessionStorage(session_id="blocks", storage_dir=str(tmp_path))
    storage.record_user_message("go")
    storage.record_assistant_message("", [{"id": "t1", "name": "FileRead", "input": {}}], [block])
    storage.record_tool_result("FileRead", "t1", "body")
    messages = messages_from_transcript(storage.replay())
    assert [m.role for m in messages] == [Role.USER, Role.ASSISTANT, Role.TOOL]
    assert messages[1].provider_blocks == [block]


def test_restore_ignores_non_message_events() -> None:
    entries = [{"type": "compaction", "tokens_before": 9}, {"type": "system", "subtype": "x"}]
    assert messages_from_transcript(entries) == []


@pytest.mark.parametrize("value", ["", "../etc", "a/b", ".."])
def test_resume_id_rejects_path_like_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("NERDVANA_RESUME", value)
    assert resume_session_id() is None


def test_resume_id_accepts_session_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERDVANA_RESUME", "a1b2c3d4")
    assert resume_session_id() == "a1b2c3d4"


def test_resume_id_argument_wins_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERDVANA_RESUME", "from-env")
    assert resume_session_id("from-arg") == "from-arg"


def test_resume_id_argument_that_is_unsafe_does_not_fall_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NERDVANA_RESUME", "from-env")
    assert resume_session_id("../etc") is None


def test_persist_false_writes_nothing(tmp_path: Path) -> None:
    storage = SessionStorage(session_id="quiet", storage_dir=str(tmp_path / "s"), persist=False)
    storage.record_user_message("hello")
    assert not (tmp_path / "s").exists()


def test_round_trip_through_storage(tmp_path: Path) -> None:
    storage = SessionStorage(session_id="rt", storage_dir=str(tmp_path))
    storage.record_user_message("hello")
    storage.record_assistant_message("hi")
    assert [m.content for m in SessionStorage(session_id="rt", storage_dir=str(tmp_path)).load_messages()] == [
        "hello",
        "hi",
    ]


# ---------------------------------------------------------------------------
# Stream limits
# ---------------------------------------------------------------------------


async def _events(delays: list[float]) -> AsyncIterator[int]:
    for index, delay in enumerate(delays):
        await asyncio.sleep(delay)
        yield index


async def _drain(source: AsyncIterator[int]) -> list[int]:
    return [item async for item in source]


async def test_guard_passes_a_healthy_stream_through() -> None:
    assert await _drain(guarded_stream(_events([0, 0, 0]), idle=1, total=5)) == [0, 1, 2]


async def test_guard_raises_on_idle_gap() -> None:
    with pytest.raises(StreamTimeoutError, match="idle"):
        await _drain(guarded_stream(_events([0, 0.5]), idle=0.05, total=5))


async def test_guard_raises_on_total_budget() -> None:
    with pytest.raises(StreamTimeoutError, match="exceeded"):
        await _drain(guarded_stream(_events([0.03] * 10), idle=1, total=0.1))


async def test_guard_with_limits_disabled_waits() -> None:
    assert await _drain(guarded_stream(_events([0.01, 0.01]), idle=0, total=0)) == [0, 1]


async def test_guard_closes_the_source_when_a_limit_fires() -> None:
    closed: list[bool] = []

    async def source() -> AsyncIterator[int]:
        try:
            yield 1
            await asyncio.sleep(1)
            yield 2
        finally:
            closed.append(True)

    with pytest.raises(StreamTimeoutError):
        await _drain(guarded_stream(source(), idle=0.05, total=5))
    assert closed == [True]


# ---------------------------------------------------------------------------
# Agent loop wiring
# ---------------------------------------------------------------------------


class _SilentProvider:
    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        await asyncio.sleep(1)
        yield ProviderEvent(type="done", stop_reason="end_turn")


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: Any, session_id: str = "life") -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    return AgentLoop(
        settings = settings,
        registry = ToolRegistry(),
        session  = SessionStorage(session_id=session_id, storage_dir=str(tmp_path / "sessions")),
    )


async def test_loop_reports_a_stalled_provider_instead_of_hanging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    loop = _loop(monkeypatch, tmp_path, _SilentProvider())
    loop.settings.session.stream_idle_timeout = 0.05
    chunks = [c async for c in loop.run("hello")]
    assert any("idle timeout" in c for c in chunks)


async def test_restore_history_loads_the_recorded_conversation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    first = SessionStorage(session_id="again", storage_dir=str(tmp_path / "sessions"))
    first.record_user_message("earlier question")
    first.record_assistant_message("earlier answer")

    loop = _loop(monkeypatch, tmp_path, _SilentProvider(), session_id="again")
    assert loop.restore_history() == 2
    assert [m.content for m in loop.state.messages] == ["earlier question", "earlier answer"]


async def test_tool_context_carries_the_session_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[str] = []

    class _Provider:
        async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
            yield ProviderEvent(type="done", stop_reason="end_turn")

    loop     = _loop(monkeypatch, tmp_path, _Provider(), session_id="sid-42")
    original = loop.tool_executor.run_batch

    async def spy(calls: Any, context: Any) -> Any:
        seen.append(context.state.get("session_id"))
        return await original(calls, context)

    monkeypatch.setattr(loop.tool_executor, "run_batch", spy)
    loop.provider = _ToolThenDone()
    async for _ in loop.run("go"):
        pass
    assert seen == ["sid-42"]


class _ToolThenDone:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ProviderEvent(type="tool_use_complete", tool_use_id="t1", tool_name="Nope", tool_input_complete={})
            yield ProviderEvent(type="done", stop_reason="tool_use")
        else:
            yield ProviderEvent(type="done", stop_reason="end_turn")
