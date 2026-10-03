"""Test doubles for the ACP agent: a scripted provider, a recording editor and an in-memory connection.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from acp._transport import memory_transport_pair
from acp.core import AgentSideConnection, ClientSideConnection, connect_to_agent
from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse

from nerdvana_cli.acp.agent import NerdvanaAcpAgent
from nerdvana_cli.acp.launch import LaunchOptions
from nerdvana_cli.core.loop.agent_loop import AgentLoop
from nerdvana_cli.providers.base import ProviderEvent


class ScriptedProvider:
    """Answers each model request with the next scripted event sequence; the last one repeats."""

    def __init__(self, turns: list[list[ProviderEvent]]) -> None:
        self.turns = turns
        self.calls = 0

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        turn = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        for event in turn:
            if event.type == "hang":
                await asyncio.Event().wait()
            yield event


def answer(*words: str) -> list[ProviderEvent]:
    """A model turn that streams *words* and ends."""
    return [
        *(ProviderEvent(type="content_delta", content=word) for word in words),
        ProviderEvent(type="usage", usage={"input_tokens": 100, "output_tokens": 7, "cache_read_tokens": 40}),
        ProviderEvent(type="done", stop_reason="end_turn"),
    ]


def tool_turn(tool_use_id: str, name: str, tool_input: dict[str, Any]) -> list[ProviderEvent]:
    """A model turn that asks for one tool call."""
    return [
        ProviderEvent(type="tool_use_complete", tool_use_id=tool_use_id, tool_name=name, tool_input_complete=tool_input),
        ProviderEvent(type="done", stop_reason="tool_use"),
    ]


class RecordingEditor:
    """An ACP client that records updates and answers permission requests from a list of option ids."""

    def __init__(self, choices: list[str] | None = None, hold: asyncio.Event | None = None) -> None:
        self.updates:     list[Any]            = []
        self.permissions: list[dict[str, Any]] = []
        self.choices                           = list(choices or [])
        self.hold                              = hold

    def on_connect(self, conn: Any) -> None:
        pass

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append(update)

    async def request_permission(self, session_id: str, tool_call: Any, options: Any, **kwargs: Any) -> RequestPermissionResponse:
        self.permissions.append({"session_id": session_id, "tool_call": tool_call, "options": options})
        if self.hold is not None:
            await self.hold.wait()
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        option = self.choices.pop(0) if self.choices else "reject_once"
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=option))

    def of_kind(self, kind: str) -> list[Any]:
        return [u for u in self.updates if u.session_update == kind]

    def message_text(self) -> str:
        return "".join(u.content.text for u in self.of_kind("agent_message_chunk"))

    async def wait_for(self, kind: str) -> None:
        """Block until an update of *kind* has arrived."""
        for _ in range(500):
            if self.of_kind(kind):
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"no {kind} update arrived")

    async def wait_for_permission(self) -> None:
        for _ in range(500):
            if self.permissions:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("no permission request arrived")


@asynccontextmanager
async def connected(agent: NerdvanaAcpAgent, editor: RecordingEditor) -> AsyncIterator[ClientSideConnection]:
    """The editor side of an in-memory connection to *agent*."""
    left, right = memory_transport_pair()
    agent_side  = AgentSideConnection(agent, left)
    editor_side = connect_to_agent(editor, right)
    try:
        yield editor_side
    finally:
        await editor_side.close()
        await agent_side.close()
        await agent.shutdown()


def use_provider(monkeypatch: pytest.MonkeyPatch, provider: ScriptedProvider) -> None:
    """Make every agent loop talk to *provider*."""
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)


def make_agent(**options: Any) -> NerdvanaAcpAgent:
    return NerdvanaAcpAgent(LaunchOptions(**options))


def isolate(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """Keep a test's data, configuration and API key apart from the machine's."""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(home / "data"))
    monkeypatch.setenv("NERDVANA_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("NERDVANA_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
