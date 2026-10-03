"""Anthropic on-demand compaction: the request, the block round trip and what the provider drops.

Payload shapes follow the compaction on demand page of the Anthropic documentation; nothing here touches the
network.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import anthropic
import pytest

try:
    import httpx2 as httpx  # the SDK's own HTTP layer from 1.0 on
except ImportError:          # pragma: no cover - older SDKs
    import httpx  # type: ignore[no-redef]

from nerdvana_cli.core.config.settings import ModelConfig, NerdvanaSettings
from nerdvana_cli.core.loop.agent_loop import AgentLoop
from nerdvana_cli.core.state.session import SessionStorage
from nerdvana_cli.core.tool import ToolRegistry
from nerdvana_cli.providers.anthropic_features import BETA_COMPACTION, BETA_TURN_EFFORT, supports_compaction
from nerdvana_cli.providers.anthropic_provider import AnthropicProvider
from nerdvana_cli.providers.base import ProviderConfig, ProviderName
from nerdvana_cli.providers.factory import create_provider
from nerdvana_cli.types import Message, Role

MODEL = "claude-opus-5-5"

BLOCK = {
    "type": "compaction",
    "content": "Summary of the conversation: the user is designing the data model for a recipe app.",
    "signature": "EuYBCkQY...",
}


def _ns(**kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


class _Events:
    def __init__(self, events: list[Any]) -> None:
        self.events = events

    def __aiter__(self) -> AsyncIterator[Any]:
        async def _iter() -> AsyncIterator[Any]:
            for event in self.events:
                yield event
        return _iter()


def _provider(model: str = MODEL, compaction: str = "on", **config: Any) -> AnthropicProvider:
    return AnthropicProvider(ProviderConfig(provider=ProviderName.ANTHROPIC, api_key="k", model=model, anthropic_compaction=compaction, **config))


def _summary_response(stop_reason: str = "compaction", content: list[Any] | None = None) -> Any:
    blocks = [_ns(**BLOCK)] if content is None else content
    usage  = _ns(input_tokens=0, output_tokens=0, iterations=[_ns(type="compaction", input_tokens=144, output_tokens=276)])
    return _ns(content=blocks, stop_reason=stop_reason, usage=usage)


HISTORY = [
    {"role": "user", "content": "I am building a recipe app."},
    {"role": "assistant", "content": "Start with Recipe, Ingredient, Step."},
    {"role": "user", "content": "Good. Now suggest field names for Recipe."},
]


# ---------------------------------------------------------------------------
# Support
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("model", "expected"), [
    ("claude-opus-5-5", True), ("claude-sonnet-5-5", True), ("claude-fable-5-1", True), ("claude-opus-4-6", True),
    ("claude-sonnet-4-6", True), ("claude-opus-4-5-20251101", False), ("claude-haiku-4-5-20251001", False), ("MiniMax-M2", False),
])
def test_the_models_that_take_the_compaction_request(model: str, expected: bool) -> None:
    assert supports_compaction(model) is expected


def test_the_provider_reports_support_only_when_the_setting_is_on() -> None:
    assert _provider().supports_server_compaction is True
    assert _provider(compaction="off").supports_server_compaction is False
    assert _provider("claude-haiku-4-5-20251001").supports_server_compaction is False


def test_the_setting_and_the_factory() -> None:
    assert ModelConfig().anthropic_compaction == "off"
    with pytest.raises(ValueError):
        ModelConfig(anthropic_compaction="auto")
    assert create_provider(provider="anthropic", model=MODEL, api_key="k", anthropic_compaction="on").supports_server_compaction is True  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# The compaction request
# ---------------------------------------------------------------------------


async def test_the_request_carries_the_parameter_the_header_and_the_conversation_unchanged() -> None:
    provider = _provider()
    create   = AsyncMock(return_value=_summary_response())
    provider._client = _ns(messages=_ns(create=create))  # type: ignore[assignment]
    tool     = _ns(name="FileRead", description_text="d", input_schema={"type": "object"}, tags=frozenset())

    await provider.compact("sys", HISTORY, [tool])

    kwargs = create.await_args.kwargs
    assert kwargs["extra_body"]["compaction"] == {"type": "summarize"}
    assert kwargs["extra_headers"] == {"anthropic-beta": BETA_COMPACTION}
    assert [m["role"] for m in kwargs["messages"]] == ["user", "assistant", "user"]
    assert kwargs["tools"][0]["name"] == "FileRead"
    assert kwargs["system"]
    assert "stream" not in kwargs
    assert "stop_sequences" not in kwargs
    assert "tool_choice" not in kwargs
    assert kwargs["timeout"] >= 60


async def test_the_result_holds_the_block_exactly_as_returned_and_counts_the_summarizing_call() -> None:
    provider = _provider()
    provider._client = _ns(messages=_ns(create=AsyncMock(return_value=_summary_response())))  # type: ignore[assignment]

    result = await provider.compact("sys", HISTORY, [])

    assert result["provider_blocks"] == [BLOCK]
    assert result["stop_reason"] == "compaction"
    assert result["content"] == ""
    assert result["usage"] == {"input_tokens": 144, "output_tokens": 276}
    assert "is_error" not in result


async def test_an_answer_without_a_summary_is_an_error_the_caller_can_continue_from() -> None:
    provider = _provider()
    provider._client = _ns(messages=_ns(create=AsyncMock(return_value=_summary_response(stop_reason="max_tokens", content=[]))))  # type: ignore[assignment]
    result = await provider.compact("sys", HISTORY, [])
    assert result["is_error"] is True
    assert "max_tokens" in result["content"]
    assert "provider_blocks" not in result


async def test_a_failed_call_is_an_error_result() -> None:
    provider = _provider()
    provider._client = _ns(messages=_ns(create=AsyncMock(side_effect=RuntimeError("boom"))))  # type: ignore[assignment]
    result = await provider.compact("sys", HISTORY, [])
    assert result["is_error"] is True
    assert "boom" in result["content"]


@pytest.mark.parametrize("provider", [_provider(compaction="off"), _provider("claude-haiku-4-5-20251001")])
async def test_no_request_is_made_when_the_setting_is_off_or_the_model_cannot(provider: AnthropicProvider) -> None:
    create = AsyncMock()
    provider._client = _ns(messages=_ns(create=create))  # type: ignore[assignment]
    result = await provider.compact("sys", HISTORY, [])
    assert result["is_error"] is True
    create.assert_not_awaited()


# ---------------------------------------------------------------------------
# The block takes the place of what it summarized
# ---------------------------------------------------------------------------


def _compacted() -> list[dict[str, Any]]:
    return [*HISTORY, {"role": "assistant", "content": "", "provider_blocks": [BLOCK]}, {"role": "user", "content": "Now do Ingredient."}]


def test_the_block_comes_first_and_the_summarized_messages_are_dropped() -> None:
    converted = _provider()._convert_messages(_compacted())
    assert converted == [
        {"role": "assistant", "content": [BLOCK]},
        {"role": "user", "content": [{"type": "text", "text": "Now do Ingredient."}]},
    ]


def test_a_turn_taken_after_the_block_follows_it_unchanged() -> None:
    history   = [*_compacted(), {"role": "assistant", "content": "Use title, servings."}, {"role": "user", "content": "And steps?"}]
    converted = _provider()._convert_messages(history)
    assert [m["role"] for m in converted] == ["assistant", "user", "assistant", "user"]
    assert converted[2]["content"] == [{"type": "text", "text": "Use title, servings."}]


def test_compacting_again_leaves_only_the_newest_block() -> None:
    newer = {"type": "compaction", "content": "Newer summary", "signature": "sig-2"}
    history = [*_compacted(), {"role": "assistant", "content": "ok"}, {"role": "user", "content": "more"},
               {"role": "assistant", "content": "", "provider_blocks": [newer]}, {"role": "user", "content": "next"}]
    converted = _provider()._convert_messages(history)
    blocks = [b for m in converted for b in m["content"] if b["type"] == "compaction"]
    assert blocks == [newer]
    assert converted[0] == {"role": "assistant", "content": [newer]}
    assert len(converted) == 2


def test_the_loop_history_is_kept_whole_so_dropping_the_block_falls_back_to_the_full_history() -> None:
    stripped = [{k: v for k, v in m.items() if k != "provider_blocks"} for m in _compacted()]
    converted = _provider()._convert_messages(stripped)
    assert [m["role"] for m in converted] == ["user", "assistant", "user"]


def test_the_effort_in_force_at_the_block_is_stated_again_after_it() -> None:
    provider = _provider()
    history  = [*HISTORY, {"role": "assistant", "content": "", "provider_blocks": [BLOCK]}, {"role": "user", "content": "next"}]
    converted = provider._convert_messages(history, {2: "low"})
    assert [m["role"] for m in converted] == ["assistant", "system", "user"]
    assert converted[1]["output_config"] == {"effort": "low"}


async def test_a_request_that_carries_the_block_has_the_beta_header_and_one_without_it_does_not() -> None:
    provider = _provider()
    create   = AsyncMock(side_effect=lambda **_: _Events([]))
    provider._client = _ns(messages=_ns(create=create))  # type: ignore[assignment]

    _ = [e async for e in provider.stream("sys", _compacted(), [])]
    assert create.await_args.kwargs["extra_headers"] == {"anthropic-beta": BETA_COMPACTION}
    assert [m["role"] for m in create.await_args.kwargs["messages"]] == ["assistant", "user"]

    _ = [e async for e in provider.stream("sys", HISTORY, [])]
    assert "extra_headers" not in create.await_args.kwargs


async def test_both_betas_are_sent_together_when_a_request_needs_both() -> None:
    provider = _provider(reasoning_effort="high")
    create   = AsyncMock(side_effect=lambda **_: _Events([]))
    provider._client = _ns(messages=_ns(create=create))  # type: ignore[assignment]
    _ = [e async for e in provider.stream("sys", [{"role": "user", "content": "q"}], [])]
    provider.set_turn_effort("low")
    _ = [e async for e in provider.stream("sys", _compacted(), [])]
    assert create.await_args.kwargs["extra_headers"] == {"anthropic-beta": f"{BETA_TURN_EFFORT},{BETA_COMPACTION}"}


# ---------------------------------------------------------------------------
# Streaming and sending
# ---------------------------------------------------------------------------


async def test_a_streamed_compaction_block_arrives_whole_and_the_stop_reason_is_kept() -> None:
    provider = _provider()
    events = [
        _ns(type="message_start", message=_ns(usage=_ns(input_tokens=0, output_tokens=0))),
        _ns(type="content_block_start", index=0, content_block=_ns(**BLOCK)),
        _ns(type="content_block_stop", index=0),
        _ns(type="message_delta", usage=_ns(output_tokens=0), delta=_ns(stop_reason="compaction")),
    ]
    provider._client = _ns(messages=_ns(create=AsyncMock(return_value=_Events(events))))  # type: ignore[assignment]
    out = [e async for e in provider.stream("sys", HISTORY, [])]
    assert [e.block for e in out if e.type == "provider_block"] == [BLOCK]
    assert [e.stop_reason for e in out if e.type == "done"] == ["compaction"]


def _real_sdk(captured: list[httpx.Request], reply: dict[str, Any]) -> anthropic.AsyncAnthropic:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=reply)

    return anthropic.AsyncAnthropic(api_key="k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_the_documented_response_is_read_by_the_real_sdk_and_sent_back_as_it_came() -> None:
    reply = {
        "id": "msg_013Zva2CMHLNnXjNJJKqJ2EF", "type": "message", "role": "assistant", "model": MODEL, "stop_sequence": None,
        "content": [BLOCK], "stop_reason": "compaction",
        "usage": {"input_tokens": 0, "output_tokens": 0, "iterations": [{"type": "compaction", "input_tokens": 144, "output_tokens": 276}]},
    }
    captured: list[httpx.Request] = []
    provider = _provider()
    provider._client = _real_sdk(captured, reply)

    result = await provider.compact("sys", HISTORY, [])

    sent = json.loads(captured[0].content)
    assert sent["compaction"] == {"type": "summarize"}
    assert captured[0].headers["anthropic-beta"] == BETA_COMPACTION
    assert result["provider_blocks"] == [BLOCK]
    assert result["usage"] == {"input_tokens": 144, "output_tokens": 276}

    history = [*HISTORY, {"role": "assistant", "content": "", "provider_blocks": result["provider_blocks"]}, {"role": "user", "content": "next"}]
    await provider.send("sys", history, [])
    body = json.loads(captured[1].content)
    assert body["messages"][0] == {"role": "assistant", "content": [BLOCK]}
    assert len(body["messages"]) == 2


async def test_send_keeps_a_compaction_block_in_the_provider_blocks() -> None:
    provider = _provider()
    provider._client = _ns(messages=_ns(create=AsyncMock(return_value=_summary_response())))  # type: ignore[assignment]
    result = await provider.send("sys", HISTORY, [])
    assert result["provider_blocks"] == [BLOCK]


# ---------------------------------------------------------------------------
# Through the agent loop
# ---------------------------------------------------------------------------


async def test_the_loop_sends_the_block_first_and_keeps_its_own_full_history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    provider = _provider()
    create   = AsyncMock(side_effect=lambda **_: _Events([
        _ns(type="content_block_start", content_block=_ns(type="text", text="")),
        _ns(type="content_block_delta", delta=_ns(type="text_delta", text="Fields: title.")),
        _ns(type="message_delta", usage=_ns(output_tokens=3), delta=_ns(stop_reason="end_turn")),
    ]))
    provider._client = _ns(messages=_ns(create=create))  # type: ignore[assignment]
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: provider)
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    loop = AgentLoop(settings=settings, registry=ToolRegistry(), session=SessionStorage(session_id="t", storage_dir=str(tmp_path / "s")))
    loop.state.messages = [
        Message(role=Role.USER, content="I am building a recipe app."),
        Message(role=Role.ASSISTANT, content="Start with Recipe."),
        Message(role=Role.ASSISTANT, content="", provider_blocks=[BLOCK]),
    ]

    async for _ in loop.run("Now do Ingredient."):
        pass

    sent = create.await_args.kwargs["messages"]
    assert sent[0] == {"role": "assistant", "content": [BLOCK]}
    assert [m["role"] for m in sent] == ["assistant", "user"]
    assert create.await_args.kwargs["extra_headers"] == {"anthropic-beta": BETA_COMPACTION}
    assert [m.content for m in loop.state.messages[:2]] == ["I am building a recipe app.", "Start with Recipe."]
    assert loop.state.messages[2].provider_blocks == [BLOCK]


def test_unquoted_yaml_on_and_off_mean_the_words() -> None:
    import yaml

    assert ModelConfig(**yaml.safe_load("anthropic_compaction: on")).anthropic_compaction == "on"
    assert ModelConfig(**yaml.safe_load("anthropic_compaction: off")).anthropic_compaction == "off"
    assert ModelConfig(**yaml.safe_load("anthropic_tool_search: off")).anthropic_tool_search == "off"
    with pytest.raises(ValueError):
        ModelConfig(**yaml.safe_load("anthropic_tool_search: on"))


async def test_the_compaction_request_keeps_the_effort_beta_when_effort_messages_are_in_the_history() -> None:
    provider = _provider(reasoning_effort="high")
    create   = AsyncMock(return_value=_summary_response())
    provider._client = _ns(messages=_ns(create=create))  # type: ignore[assignment]
    await provider.compact("sys", [HISTORY[0]], [])
    provider.set_turn_effort("low")
    await provider.compact("sys", HISTORY, [])
    assert create.await_args.kwargs["extra_headers"] == {"anthropic-beta": f"{BETA_TURN_EFFORT},{BETA_COMPACTION}"}
    assert create.await_args.kwargs["extra_body"] == {"output_config": {"effort": "high"}, "compaction": {"type": "summarize"}}
