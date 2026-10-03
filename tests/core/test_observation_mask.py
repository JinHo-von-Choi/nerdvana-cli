"""Observation masking: old read-type tool output is cleared in batches and nothing else is touched.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.context.observation_mask import MaskResult, mask_observations, placeholder_for
from nerdvana_cli.core.state import signals
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import Message, Role

BIG = "x" * 4000   # about 1000 estimated tokens


def _call(call_id: str, name: str, **arguments: Any) -> Message:
    return Message(role=Role.ASSISTANT, content="", tool_uses=[{"id": call_id, "name": name, "input": arguments}])


def _result(call_id: str, text: str = BIG, *, is_error: bool = False) -> Message:
    return Message(role=Role.TOOL, content=text, tool_use_id=call_id, is_error=is_error)


def _history(*calls: tuple[str, str, dict[str, Any], str]) -> list[Message]:
    """(call id, tool name, arguments, result text) per call, each as an assistant and a tool message."""
    messages: list[Message] = [Message(role=Role.USER, content="start")]
    for call_id, name, arguments, text in calls:
        messages += [_call(call_id, name, **arguments), _result(call_id, text)]
    return messages


def _texts(messages: list[Message]) -> list[Any]:
    return [m.content for m in messages]


def _mask(messages: list[Message], keep_last: int = 0, trigger: int = 0) -> MaskResult:
    return mask_observations(messages, keep_last=keep_last, trigger_tokens=trigger)


# ---------------------------------------------------------------------------
# What is cleared and how
# ---------------------------------------------------------------------------


def test_old_read_results_are_replaced_and_the_pairing_survives() -> None:
    messages = _history(("a", "Grep", {"pattern": "x"}, BIG), ("b", "Bash", {"command": "ls"}, BIG), ("c", "Glob", {}, BIG))
    before   = [(m.role, m.tool_use_id, m.tool_uses) for m in messages]
    result   = _mask(messages, keep_last=1)
    assert result.masked == 2
    assert result.tokens_saved > 1900
    assert messages[2].content == placeholder_for("Grep", len(BIG))
    assert messages[4].content == placeholder_for("Bash", len(BIG))
    assert messages[6].content == BIG
    assert [(m.role, m.tool_use_id, m.tool_uses) for m in messages] == before


def test_mcp_and_web_output_is_cleared() -> None:
    messages = _history(("a", "mcp__docs__search", {}, BIG), ("b", "WebFetch", {}, BIG), ("c", "Glob", {}, BIG))
    assert _mask(messages, keep_last=1).masked == 2


def test_results_of_edit_write_and_todo_tools_are_kept() -> None:
    messages = _history(
        ("a", "FileWrite", {"path": "a.py"}, BIG), ("b", "FileEdit", {"path": "a.py"}, BIG),
        ("c", "replace_symbol_body", {}, BIG), ("d", "TodoWrite", {}, BIG), ("e", "lsp_diagnostics", {}, BIG),
        ("f", "Glob", {}, BIG),
    )
    before = _texts(messages)
    assert _mask(messages, keep_last=1).masked == 0
    assert _texts(messages) == before


def test_the_last_results_are_kept() -> None:
    messages = _history(*[(f"c{i}", "Grep", {}, BIG) for i in range(5)])
    assert _mask(messages, keep_last=3).masked == 2
    assert [m.content == BIG for m in messages if m.role == Role.TOOL] == [False, False, True, True, True]


def test_an_old_anchored_read_is_cleared_and_the_newest_results_stay() -> None:
    anchored = "1#a1b2c3    import os\n2#d4e5f6    import sys"
    messages = _history(
        ("a", "FileRead", {"path": "x.py"}, anchored + BIG), ("b", "FileRead", {"path": "y.py"}, anchored + BIG),
        ("c", "FileRead", {"path": "x.py"}, anchored + BIG), ("d", "Grep", {}, BIG),
    )
    assert _mask(messages, keep_last=2).masked == 2
    assert messages[2].content == placeholder_for("FileRead", len(anchored + BIG))
    assert messages[4].content == placeholder_for("FileRead", len(anchored + BIG))
    assert messages[6].content == anchored + BIG   # one of the last two calls
    assert messages[8].content == BIG


def test_error_results_and_activated_skills_are_kept() -> None:
    skill    = "<skill_content name='x'>" + BIG + "</skill_content>"
    messages = _history(("a", "Bash", {}, BIG), ("b", "Grep", {}, skill), ("c", "Glob", {}, BIG))
    messages[2].is_error = True
    assert _mask(messages, keep_last=1).masked == 0
    assert messages[2].content == BIG
    assert messages[4].content == skill


def test_unknown_tool_names_and_orphan_results_are_kept() -> None:
    messages = _history(("a", "SomethingNew", {}, BIG), ("b", "Glob", {}, BIG))
    messages.insert(1, _result("ghost"))
    assert _mask(messages, keep_last=1).masked == 0


# ---------------------------------------------------------------------------
# Batching and idempotency
# ---------------------------------------------------------------------------


def test_nothing_changes_below_the_trigger() -> None:
    messages = _history(*[(f"c{i}", "Grep", {}, BIG) for i in range(4)])
    before   = _texts(messages)
    assert _mask(messages, keep_last=1, trigger=5000) == MaskResult(0, 0)
    assert _texts(messages) == before


def test_one_batch_above_the_trigger_then_the_history_is_stable() -> None:
    messages = _history(*[(f"c{i}", "Grep", {}, BIG) for i in range(6)])
    first    = _mask(messages, keep_last=2, trigger=3500)
    assert first.masked == 4
    settled = _texts(messages)
    assert _mask(messages, keep_last=2, trigger=3500) == MaskResult(0, 0)
    assert _texts(messages) == settled


def test_a_new_batch_waits_for_enough_new_output() -> None:
    messages = _history(*[(f"c{i}", "Grep", {}, BIG) for i in range(4)])
    assert _mask(messages, keep_last=1, trigger=2500).masked == 3
    for i in range(4, 6):
        messages += [_call(f"c{i}", "Grep"), _result(f"c{i}")]
    settled = _texts(messages)
    assert _mask(messages, keep_last=1, trigger=2500) == MaskResult(0, 0)   # only 2000 tokens are clearable
    assert _texts(messages) == settled
    messages += [_call("c6", "Grep"), _result("c6"), _call("c7", "Grep"), _result("c7")]
    assert _mask(messages, keep_last=1, trigger=2500).masked == 4


def test_a_placeholder_is_not_cleared_again() -> None:
    messages = _history(("a", "Grep", {}, BIG), ("b", "Glob", {}, BIG))
    _mask(messages, keep_last=1)
    cleared = messages[2].content
    assert _mask(messages, keep_last=0).masked == 1   # only b is left to clear
    assert messages[2].content == cleared


# ---------------------------------------------------------------------------
# In the loop
# ---------------------------------------------------------------------------


class _Quiet:
    def __init__(self) -> None:
        self.payloads: list[list[dict[str, Any]]] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.payloads.append([dict(m) for m in messages])
        yield ProviderEvent(type="content_delta", content="ok")
        yield ProviderEvent(type="done", stop_reason="end_turn")


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **session: Any) -> tuple[AgentLoop, _Quiet]:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    provider = _Quiet()
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    settings.model.provider = "anthropic"
    settings.model.model    = "claude-haiku-4-5-20251001"
    for key, value in session.items():
        setattr(settings.session, key, value)
    loop = AgentLoop(settings=settings, registry=ToolRegistry(), session=SessionStorage(session_id="mask", storage_dir=str(tmp_path / "s")))
    loop.state.messages = _history(*[(f"c{i}", "Grep", {}, BIG) for i in range(12)])
    return loop, provider


async def _drain(loop: AgentLoop) -> None:
    [_ async for _ in loop.run("go")]


def test_masking_is_off_by_default() -> None:
    session = NerdvanaSettings().session
    assert session.observation_masking is False
    assert (session.mask_keep_last, session.mask_trigger_tokens) == (6, 20_000)


async def test_off_by_default_no_message_changes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, provider = _loop(monkeypatch, tmp_path)
    before = _texts(loop.state.messages)
    await _drain(loop)
    assert _texts(loop.state.messages)[: len(before)] == before
    assert all(m["content"] == BIG for m in provider.payloads[0] if m["role"] == "tool")
    assert signals.OBSERVATIONS_MASKED not in loop.signal_summary()


async def test_enabled_it_clears_old_output_once_and_counts_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop, provider = _loop(monkeypatch, tmp_path, observation_masking=True, mask_keep_last=2, mask_trigger_tokens=3000)
    await _drain(loop)
    sent = [m["content"] for m in provider.payloads[0] if m["role"] == "tool"]
    assert sent.count(placeholder_for("Grep", len(BIG))) == 10
    assert sent[-2:] == [BIG, BIG]
    assert loop.signal_summary()[signals.OBSERVATIONS_MASKED] == 10
    await _drain(loop)
    assert loop.signal_summary()[signals.OBSERVATIONS_MASKED] == 10
