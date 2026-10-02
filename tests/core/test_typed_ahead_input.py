"""Text typed while the agent works reaches it at the next step.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import BaseTool, ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.types import Role, ToolResult
from nerdvana_cli.ui.app import NerdvanaApp


class _Typing(BaseTool[Any]):
    """A tool during whose run the user types something."""

    name             = "Echo"
    description_text = "echo"

    def __init__(self, loop_ref: list[AgentLoop], typed: str) -> None:
        self.loop_ref = loop_ref
        self.typed    = typed

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        self.loop_ref[0].queue_input(self.typed)
        return ToolResult(tool_use_id="", content="tool output")


class _Script:
    def __init__(self, responses: list[list[ProviderEvent]], on_request: Any = None) -> None:
        self.responses  = list(responses)
        self.on_request = on_request
        self.payloads: list[list[dict[str, Any]]] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.payloads.append([dict(m) for m in messages])
        if self.on_request:
            self.on_request(len(self.payloads))
        for event in self.responses.pop(0) if self.responses else [ProviderEvent(type="done", stop_reason="end_turn")]:
            yield event


def _tool_turn() -> list[ProviderEvent]:
    return [
        ProviderEvent(type="tool_use_complete", tool_use_id="c1", tool_name="Echo", tool_input_complete={}),
        ProviderEvent(type="done", stop_reason="tool_use"),
    ]


def _end() -> list[ProviderEvent]:
    return [ProviderEvent(type="content_delta", content="ok"), ProviderEvent(type="done", stop_reason="end_turn")]


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: _Script, tools: list[BaseTool[Any]] | None = None) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    registry = ToolRegistry()
    for tool in tools or []:
        registry.register(tool)
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    return AgentLoop(
        settings = settings,
        registry = registry,
        session  = SessionStorage(session_id="typed", storage_dir=str(tmp_path / "sessions")),
    )


def _roles_and_text(payload: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(m["role"], str(m.get("content", ""))[:40]) for m in payload]


async def test_text_typed_during_a_tool_call_arrives_after_its_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ref: list[AgentLoop] = []
    provider = _Script([_tool_turn(), _end()])
    loop     = _loop(monkeypatch, tmp_path, provider, [_Typing(ref, "actually use pytest")])
    ref.append(loop)

    async for _ in loop.run("go"):
        pass

    second = provider.payloads[1]
    roles  = [m["role"] for m in second]
    assert roles[-2:] == ["tool", "user"]
    assert second[-1]["content"] == "actually use pytest"
    assert not loop.has_queued_input()


async def test_text_typed_just_before_the_model_finishes_keeps_the_run_going(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    holder: list[AgentLoop] = []

    def on_request(count: int) -> None:
        if count == 1:
            holder[0].queue_input("one more thing")

    provider = _Script([_end(), _end()], on_request=on_request)
    loop     = _loop(monkeypatch, tmp_path, provider)
    holder.append(loop)

    async for _ in loop.run("go"):
        pass

    assert len(provider.payloads) == 2
    assert provider.payloads[1][-1] == {"role": "user", "content": "one more thing"}
    assert [m.role for m in loop.state.messages][-3:] == [Role.ASSISTANT, Role.USER, Role.ASSISTANT]


async def test_several_typed_lines_keep_their_order_and_are_recorded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    holder: list[AgentLoop] = []

    def on_request(count: int) -> None:
        if count == 1:
            holder[0].queue_input("first")
            holder[0].queue_input("   ")
            holder[0].queue_input("second")

    provider = _Script([_end(), _end()], on_request=on_request)
    loop     = _loop(monkeypatch, tmp_path, provider)
    holder.append(loop)
    async for _ in loop.run("go"):
        pass

    assert [m["content"] for m in provider.payloads[1][-2:]] == ["first", "second"]
    recorded = [e["content"] for e in loop.session.replay() if e["type"] == "user"]
    assert recorded[-2:] == ["first", "second"]


async def test_queued_text_is_dropped_when_the_session_is_reset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop = _loop(monkeypatch, tmp_path, _Script([]))
    loop.queue_input("stale")
    loop.reset_session()
    assert not loop.has_queued_input()
    assert loop.take_queued_input() == []


def test_blank_input_is_not_queued(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop = _loop(monkeypatch, tmp_path, _Script([]))
    loop.queue_input("  \n ")
    assert not loop.has_queued_input()


# ---------------------------------------------------------------------------
# App: leftover input after the run ended
# ---------------------------------------------------------------------------


class _FakeApp:
    _drain_queued_input = NerdvanaApp._drain_queued_input

    def __init__(self, loop: Any, generating: bool = False) -> None:
        self._agent_loop    = loop
        self._is_generating = generating
        self.messages: list[str] = []
        self.started:  list[str] = []

    def _add_chat_message(self, markup: str, **_: Any) -> None:
        self.messages.append(markup)

    def _generate_response(self, prompt: str) -> None:
        self.started.append(prompt)


def test_leftover_input_becomes_the_next_turn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop = _loop(monkeypatch, tmp_path, _Script([]))
    loop.queue_input("first")
    loop.queue_input("second")
    app = _FakeApp(loop)
    app._drain_queued_input()
    assert app.started == ["first\n\nsecond"]
    assert not loop.has_queued_input()


def test_nothing_starts_while_a_run_is_still_active_or_nothing_is_queued(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    loop = _loop(monkeypatch, tmp_path, _Script([]))
    loop.queue_input("later")
    busy = _FakeApp(loop, generating=True)
    busy._drain_queued_input()
    assert busy.started == []
    assert loop.has_queued_input()
    loop.take_queued_input()
    _FakeApp(loop)._drain_queued_input()
