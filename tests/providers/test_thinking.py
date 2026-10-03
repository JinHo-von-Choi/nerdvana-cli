"""Extended thinking: request options per model family and thinking blocks kept across tool turns.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.context.context_budget import message_tokens
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.tool import BaseTool, ToolRegistry
from nerdvana_cli.providers.anthropic_provider import AnthropicProvider, request_options
from nerdvana_cli.providers.base import ProviderConfig, ProviderEvent, ProviderName
from nerdvana_cli.types import Message, Role, ToolResult

# ---------------------------------------------------------------------------
# Request options
# ---------------------------------------------------------------------------


def _options(model: str, extended: bool = False, show: bool = False, budget: int = 8000, max_tokens: int = 4096) -> dict[str, Any]:
    return request_options(model, max_tokens, 0.5, extended, budget, show)


@pytest.mark.parametrize("model", ["claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"])
def test_models_that_think_by_default_get_adaptive_thinking_and_no_temperature(model: str) -> None:
    options = _options(model)
    assert options["thinking"] == {"type": "adaptive", "display": "omitted"}
    assert "temperature" not in options
    assert options["max_tokens"] == 4096


def test_show_thinking_asks_for_summaries() -> None:
    assert _options("claude-sonnet-5-5", show=True)["thinking"]["display"] == "summarized"


@pytest.mark.parametrize("model", ["claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6"])
def test_opt_in_models_think_only_when_asked(model: str) -> None:
    assert "thinking" not in _options(model)
    assert _options(model, extended=True)["thinking"]["type"] == "adaptive"


def test_models_with_a_fixed_sampling_policy_never_receive_a_temperature() -> None:
    assert "temperature" not in _options("claude-opus-4-7", extended=True)
    assert _options("claude-opus-4-6")["temperature"] == 0.5


def test_manual_budget_models_use_a_budget_below_max_tokens_and_no_temperature() -> None:
    options = _options("claude-haiku-4-5-20251001", extended=True, budget=8000, max_tokens=4096)
    assert options["thinking"] == {"type": "enabled", "budget_tokens": 8000}
    assert options["max_tokens"] > 8000
    assert "temperature" not in options


def test_manual_budget_models_without_the_switch_send_no_thinking() -> None:
    options = _options("claude-haiku-4-5-20251001")
    assert "thinking" not in options
    assert options["temperature"] == 0.5


def test_unknown_model_ids_are_left_alone() -> None:
    assert _options("MiniMax-M2", extended=True) == {"max_tokens": 4096, "temperature": 0.5}


# ---------------------------------------------------------------------------
# Streaming and conversion
# ---------------------------------------------------------------------------


class _Events:
    def __init__(self, events: list[Any]) -> None:
        self.events = events

    def __aiter__(self) -> AsyncIterator[Any]:
        async def _iter() -> AsyncIterator[Any]:
            for event in self.events:
                yield event
        return _iter()


def _ns(**kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


def _provider(events: list[Any]) -> AnthropicProvider:
    provider = AnthropicProvider(ProviderConfig(provider=ProviderName.ANTHROPIC, api_key="k", model="claude-sonnet-5-5"))
    provider._client = _ns(messages=_ns(create=AsyncMock(return_value=_Events(events))))  # type: ignore[assignment]
    return provider


async def _stream(provider: AnthropicProvider) -> list[ProviderEvent]:
    return [e async for e in provider.stream("s", [{"role": "user", "content": "go"}], [])]


async def test_a_streamed_thinking_block_is_captured_with_its_signature() -> None:
    provider = _provider([
        _ns(type="content_block_start", content_block=_ns(type="thinking")),
        _ns(type="content_block_delta", delta=_ns(type="thinking_delta", thinking="let me ")),
        _ns(type="content_block_delta", delta=_ns(type="thinking_delta", thinking="think")),
        _ns(type="content_block_delta", delta=_ns(type="signature_delta", signature="sig-1")),
        _ns(type="content_block_stop"),
        _ns(type="content_block_start", content_block=_ns(type="redacted_thinking", data="opaque")),
        _ns(type="message_delta", usage=_ns(output_tokens=3), delta=_ns(stop_reason="end_turn")),
    ])
    events = await _stream(provider)
    blocks = [e.block for e in events if e.type == "provider_block"]
    assert blocks == [
        {"type": "thinking", "thinking": "let me think", "signature": "sig-1"},
        {"type": "redacted_thinking", "data": "opaque"},
    ]
    assert "".join(e.thinking for e in events if e.type == "thinking_delta") == "let me think"


async def test_send_collects_thinking_blocks() -> None:
    provider = AnthropicProvider(ProviderConfig(provider=ProviderName.ANTHROPIC, api_key="k", model="claude-sonnet-5-5"))
    response = _ns(
        content=[_ns(type="thinking", thinking="hm", signature="s"), _ns(type="text", text="ok")],
        stop_reason="end_turn",
        usage=_ns(input_tokens=1, output_tokens=1),
    )
    provider._client = _ns(messages=_ns(create=AsyncMock(return_value=response)))  # type: ignore[assignment]
    result = await provider.send("s", [{"role": "user", "content": "go"}], [])
    assert result["provider_blocks"] == [{"type": "thinking", "thinking": "hm", "signature": "s"}]
    assert result["content"] == "ok"


def test_conversion_puts_thinking_blocks_before_the_tool_calls_unchanged() -> None:
    provider = _provider([])
    block    = {"type": "thinking", "thinking": "hm", "signature": "s"}
    converted = provider._convert_messages([
        {"role": "assistant", "content": "[tool execution]", "tool_uses": [{"id": "t1", "name": "A", "input": {}}], "provider_blocks": [block]},
    ])
    content = converted[0]["content"]
    assert content[0] == block
    assert content[1]["type"] == "tool_use"
    assert all(b["type"] != "text" for b in content)


def test_conversion_does_not_mutate_the_stored_blocks() -> None:
    provider = _provider([])
    block    = {"type": "thinking", "thinking": "hm", "signature": "s"}
    converted = provider._convert_messages([{"role": "assistant", "content": "hi", "provider_blocks": [block]}])
    converted[0]["content"][0]["extra"] = 1
    assert "extra" not in block


def test_provider_blocks_count_toward_the_context_estimate() -> None:
    plain = Message(role=Role.ASSISTANT, content="hi")
    rich  = Message(role=Role.ASSISTANT, content="hi", provider_blocks=[{"type": "thinking", "thinking": "x" * 4000, "signature": "s"}])
    assert message_tokens([rich]) > message_tokens([plain]) + 500


# ---------------------------------------------------------------------------
# The loop keeps the blocks
# ---------------------------------------------------------------------------

THINKING = {"type": "thinking", "thinking": "plan", "signature": "sig"}


class _Echo(BaseTool[Any]):
    name             = "Echo"
    description_text = "echo"

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="out")


class _Script:
    def __init__(self, responses: list[list[ProviderEvent]]) -> None:
        self.responses = list(responses)
        self.payloads: list[list[dict[str, Any]]] = []

    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        self.payloads.append([dict(m) for m in messages])
        for event in self.responses.pop(0):
            yield event


def _loop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: Any) -> AgentLoop:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    registry = ToolRegistry()
    registry.register(_Echo())
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    return AgentLoop(settings=settings, registry=registry, session=SessionStorage(session_id="t", storage_dir=str(tmp_path / "s")))


async def test_thinking_blocks_of_a_tool_turn_are_sent_back_with_the_next_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _Script([
        [
            ProviderEvent(type="provider_block", block=THINKING),
            ProviderEvent(type="tool_use_complete", tool_use_id="c1", tool_name="Echo", tool_input_complete={}),
            ProviderEvent(type="done", stop_reason="tool_use"),
        ],
        [ProviderEvent(type="content_delta", content="done"), ProviderEvent(type="done", stop_reason="end_turn")],
    ])
    loop = _loop(monkeypatch, tmp_path, provider)

    async for _ in loop.run("go"):
        pass

    assistant = [m for m in provider.payloads[1] if m["role"] == "assistant"][0]
    assert assistant["provider_blocks"] == [THINKING]


async def test_ultrawork_rebuilds_the_provider_and_restores_it_afterwards(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    built: list[bool] = []
    provider = _Script([[ProviderEvent(type="content_delta", content="ok"), ProviderEvent(type="done", stop_reason="end_turn")]])
    loop     = _loop(monkeypatch, tmp_path, provider)

    def create(self: AgentLoop) -> Any:
        built.append(self.settings.model.extended_thinking)
        return provider

    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", create)
    async for _ in loop.run("ultrawork fix it"):
        pass

    assert built[0] is True
    assert built[-1] is False
    assert loop.settings.model.extended_thinking is False


async def test_a_tool_turn_without_text_is_recorded_with_its_thinking_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from nerdvana_cli.core.session import messages_from_transcript

    provider = _Script([
        [
            ProviderEvent(type="provider_block", block=THINKING),
            ProviderEvent(type="tool_use_complete", tool_use_id="c1", tool_name="Echo", tool_input_complete={}),
            ProviderEvent(type="done", stop_reason="tool_use"),
        ],
        [ProviderEvent(type="content_delta", content="done"), ProviderEvent(type="done", stop_reason="end_turn")],
    ])
    loop = _loop(monkeypatch, tmp_path, provider)

    async for _ in loop.run("go"):
        pass

    restored = messages_from_transcript(loop.session.replay())
    assert [m.role for m in restored] == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
    assert restored[1].provider_blocks == [THINKING]
    assert restored[1].tool_uses[0]["id"] == "c1"
