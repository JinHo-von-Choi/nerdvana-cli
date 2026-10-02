"""Prompt caching: breakpoints on the Anthropic request and cached-token usage.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nerdvana_cli.providers.anthropic_provider import AnthropicProvider, _usage_dict, with_cache_breakpoints
from nerdvana_cli.providers.base import ProviderConfig, ProviderEvent, ProviderName
from nerdvana_cli.providers.gemini_provider import GeminiProvider
from nerdvana_cli.providers.gemini_provider import _usage_dict as gemini_usage
from nerdvana_cli.providers.openai_provider import OpenAIProvider
from nerdvana_cli.providers.openai_provider import _usage_dict as openai_usage

TOOLS    = [{"name": "A", "description": "a", "input_schema": {}}, {"name": "B", "description": "b", "input_schema": {}}]
MESSAGES = [
    {"role": "user", "content": [{"type": "text", "text": "hello"}]},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "A", "input": {}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
]


def _breakpoints(system: Any, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> int:
    count = 0
    if isinstance(system, list):
        count += sum(1 for block in system if "cache_control" in block)
    count += sum(1 for tool in tools if "cache_control" in tool)
    for message in messages:
        if isinstance(message["content"], list):
            count += sum(1 for block in message["content"] if "cache_control" in block)
    return count


def test_breakpoints_go_on_the_last_tool_the_system_prompt_and_the_last_block() -> None:
    system, tools, messages = with_cache_breakpoints("sys", TOOLS, MESSAGES)
    assert "cache_control" not in tools[0]
    assert tools[1]["cache_control"] == {"type": "ephemeral"}
    assert system == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    assert "cache_control" not in messages[0]["content"][0]
    assert messages[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}


def test_at_most_four_breakpoints_are_used() -> None:
    assert _breakpoints(*with_cache_breakpoints("sys", TOOLS, MESSAGES)) <= 4


def test_the_inputs_are_not_modified() -> None:
    before = (repr(TOOLS), repr(MESSAGES))
    with_cache_breakpoints("sys", TOOLS, MESSAGES)
    assert (repr(TOOLS), repr(MESSAGES)) == before


def test_without_tools_or_system_only_the_conversation_is_marked() -> None:
    system, tools, messages = with_cache_breakpoints("", [], MESSAGES)
    assert system == ""
    assert tools == []
    assert _breakpoints(system, tools, messages) == 1


def test_a_trailing_block_that_cannot_be_cached_is_skipped_for_an_earlier_one() -> None:
    messages = [{"role": "assistant", "content": [{"type": "text", "text": "x"}, {"type": "thinking", "thinking": "y"}]}]
    _, _, marked = with_cache_breakpoints("s", [], messages)
    assert "cache_control" in marked[0]["content"][0]
    assert "cache_control" not in marked[0]["content"][1]


# ---------------------------------------------------------------------------
# What the provider sends
# ---------------------------------------------------------------------------


class _Events:
    def __init__(self, events: list[Any]) -> None:
        self.events = events

    def __aiter__(self) -> AsyncIterator[Any]:
        async def _iter() -> AsyncIterator[Any]:
            for event in self.events:
                yield event
        return _iter()


def _stream_events(usage_start: Any, output_tokens: int = 20) -> list[Any]:
    return [
        SimpleNamespace(type="message_start", message=SimpleNamespace(usage=usage_start)),
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="hi")),
        SimpleNamespace(
            type="message_delta",
            usage=SimpleNamespace(output_tokens=output_tokens),
            delta=SimpleNamespace(stop_reason="end_turn"),
        ),
    ]


def _provider(caching: bool, events: list[Any] | None = None) -> tuple[AnthropicProvider, AsyncMock]:
    provider = AnthropicProvider(ProviderConfig(provider=ProviderName.ANTHROPIC, api_key="k", model="m", prompt_caching=caching))
    create   = AsyncMock(return_value=_Events(events or _stream_events(SimpleNamespace(input_tokens=5))))
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))  # type: ignore[assignment]
    return provider, create


class _Spec:
    name             = "A"
    description_text = "a"
    input_schema: dict[str, Any] = {"type": "object"}


async def _run(provider: AnthropicProvider) -> list[ProviderEvent]:
    return [e async for e in provider.stream("sys prompt", [{"role": "user", "content": "go"}], [_Spec()])]


async def test_a_streamed_request_carries_breakpoints_when_caching_is_on() -> None:
    provider, create = _provider(True)
    await _run(provider)
    kwargs = create.await_args.kwargs
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}


async def test_a_streamed_request_is_plain_when_caching_is_off() -> None:
    provider, create = _provider(False)
    await _run(provider)
    kwargs = create.await_args.kwargs
    assert kwargs["system"] == "sys prompt"
    assert "cache_control" not in kwargs["tools"][-1]
    assert "cache_control" not in repr(kwargs["messages"])


async def test_send_follows_the_same_switch() -> None:
    provider = AnthropicProvider(ProviderConfig(provider=ProviderName.ANTHROPIC, api_key="k", model="m"))
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=3, output_tokens=2, cache_read_input_tokens=100, cache_creation_input_tokens=0))
    create   = AsyncMock(return_value=response)
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))  # type: ignore[assignment]

    result = await provider.send("sys", [{"role": "user", "content": "go"}], [_Spec()])

    assert create.await_args.kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert result["usage"] == {"input_tokens": 103, "output_tokens": 2, "cache_read_tokens": 100}


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


async def test_streamed_usage_counts_the_whole_prompt_and_reports_the_cached_part() -> None:
    start = SimpleNamespace(input_tokens=10, cache_creation_input_tokens=200, cache_read_input_tokens=3000)
    provider, _ = _provider(True, _stream_events(start, output_tokens=40))
    events = await _run(provider)
    (usage,) = [e.usage for e in events if e.type == "usage"]
    assert usage == {"input_tokens": 3210, "output_tokens": 40, "cache_read_tokens": 3000, "cache_write_tokens": 200}


async def test_usage_without_cache_activity_keeps_the_two_original_keys() -> None:
    provider, _ = _provider(True, _stream_events(SimpleNamespace(input_tokens=12), output_tokens=3))
    events = await _run(provider)
    (usage,) = [e.usage for e in events if e.type == "usage"]
    assert usage == {"input_tokens": 12, "output_tokens": 3}


def test_non_numeric_counters_are_ignored() -> None:
    assert _usage_dict(SimpleNamespace(input_tokens=5, cache_read_input_tokens=object(), output_tokens=None)) == {
        "input_tokens": 5,
        "output_tokens": 0,
    }


def test_openai_cached_tokens_are_reported() -> None:
    usage = SimpleNamespace(prompt_tokens=2000, completion_tokens=50, prompt_tokens_details=SimpleNamespace(cached_tokens=1536))
    assert openai_usage(usage) == {"input_tokens": 2000, "output_tokens": 50, "cache_read_tokens": 1536}


def test_deepseek_style_cache_hits_are_reported() -> None:
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=5, prompt_cache_hit_tokens=64)
    assert openai_usage(usage)["cache_read_tokens"] == 64


def test_openai_usage_without_details_is_unchanged() -> None:
    assert openai_usage(SimpleNamespace(prompt_tokens=7, completion_tokens=3)) == {"input_tokens": 7, "output_tokens": 3}


def test_gemini_cached_content_tokens_are_reported() -> None:
    meta = SimpleNamespace(prompt_token_count=900, candidates_token_count=30, cached_content_token_count=512)
    assert gemini_usage(meta) == {"input_tokens": 900, "output_tokens": 30, "cache_read_tokens": 512}


def test_providers_expose_the_switch_in_their_config() -> None:
    config = ProviderConfig(provider=ProviderName.OPENAI)
    assert config.prompt_caching is True
    assert OpenAIProvider(config).config.prompt_caching is True
    assert GeminiProvider(config).config.prompt_caching is True


@pytest.mark.parametrize("caching", [True, False])
def test_the_factory_passes_the_switch_on(caching: bool) -> None:
    from nerdvana_cli.providers.factory import create_provider

    provider = create_provider(provider="anthropic", api_key="k", prompt_caching=caching)
    assert provider.config.prompt_caching is caching
