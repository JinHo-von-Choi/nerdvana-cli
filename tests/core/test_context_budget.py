"""Context window accounting: provider anchor plus estimated tail.

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
from nerdvana_cli.core.context_budget import ContextBudget, message_tokens, request_overhead
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.tool import BaseTool, ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import Message, Role, ToolResult


class _Schema(BaseTool[Any]):
    name             = "Wide"
    description_text = "d" * 4_000
    input_schema     = {"type": "object", "properties": {"x": {"type": "string", "description": "e" * 4_000}}}

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="")


def _msgs(*texts: str) -> list[Message]:
    return [Message(role=Role.USER, content=t) for t in texts]


def test_estimate_includes_system_prompt_and_tool_schemas() -> None:
    budget = ContextBudget()
    budget.set_overhead("s" * 4_000, [_Schema()])
    assert budget.current(_msgs("hi")) >= 3_000
    assert request_overhead("s" * 4_000, [_Schema()]) >= 3_000


def test_provider_usage_becomes_the_anchor() -> None:
    budget   = ContextBudget()
    messages = _msgs("a" * 400, "b" * 400)
    budget.record_usage(50_000, messages_sent=2)
    assert budget.current(messages) == 50_000
    messages.append(Message(role=Role.ASSISTANT, content="c" * 400))
    assert budget.current(messages) == 50_000 + 100


def test_reset_falls_back_to_the_estimate() -> None:
    budget   = ContextBudget()
    messages = _msgs("a" * 400)
    budget.record_usage(50_000, messages_sent=1)
    budget.reset()
    assert budget.current(messages) == message_tokens(messages)


def test_anchor_is_ignored_when_history_shrank_below_it() -> None:
    budget = ContextBudget()
    budget.record_usage(50_000, messages_sent=5)
    assert budget.current(_msgs("a" * 40)) == 10


def test_non_latin_messages_are_not_undercounted() -> None:
    assert message_tokens(_msgs("가" * 1_000)) == 1_000


class _Reporting:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.calls += 1
        yield ProviderEvent(type="content_delta", content="ok")
        yield ProviderEvent(type="usage", usage={"input_tokens": 150_000, "output_tokens": 10})
        yield ProviderEvent(type="done", stop_reason="end_turn")


async def test_reported_usage_drives_compaction_on_the_next_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    provider = _Reporting()
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    compactions: list[int] = []

    async def _compact(self: AgentLoop, cur_toks: int, thr: int) -> AsyncIterator[str]:
        compactions.append(cur_toks)
        self._context_budget.reset()
        if False:
            yield ""

    monkeypatch.setattr(AgentLoop, "_maybe_compact_messages", _compact)
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    settings.session.max_context_tokens = 160_000
    loop = AgentLoop(
        settings = settings,
        registry = ToolRegistry(),
        session  = SessionStorage(session_id="budget", storage_dir=str(tmp_path / "sessions")),
    )

    async for _ in loop.run("first"):
        pass
    assert compactions == []

    async for _ in loop.run("second"):
        pass
    assert len(compactions) == 1
    assert compactions[0] >= 150_000
