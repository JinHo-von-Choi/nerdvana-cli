"""``reasoning_effort`` reaches OpenAI requests and Gemini thinking configuration, and only when set.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nerdvana_cli.providers.base import ProviderConfig, ProviderName
from nerdvana_cli.providers.factory import create_provider
from nerdvana_cli.providers.gemini_provider import GeminiProvider
from nerdvana_cli.providers.openai_provider import OpenAIProvider


def _openai(effort: str) -> tuple[OpenAIProvider, AsyncMock]:
    provider = OpenAIProvider(ProviderConfig(provider=ProviderName.OPENAI, model="gpt-5.6", api_key="k", reasoning_effort=effort))
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )
    create = AsyncMock(return_value=response)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))  # type: ignore[assignment]
    return provider, create


async def test_openai_send_carries_the_effort_when_set() -> None:
    provider, create = _openai("high")
    await provider.send("sys", [{"role": "user", "content": "q"}], [])
    assert create.await_args.kwargs["reasoning_effort"] == "high"


async def test_openai_send_leaves_the_request_alone_when_unset() -> None:
    provider, create = _openai("")
    await provider.send("sys", [{"role": "user", "content": "q"}], [])
    assert "reasoning_effort" not in create.await_args.kwargs


async def test_openai_stream_carries_the_effort_when_set() -> None:
    provider, create = _openai("xhigh")

    async def _empty() -> Any:
        return
        yield

    create.return_value = _empty()
    _ = [event async for event in provider.stream("sys", [{"role": "user", "content": "q"}], [])]
    assert create.await_args.kwargs["reasoning_effort"] == "xhigh"


class _Models:
    def __init__(self) -> None:
        self.configs: list[Any] = []

    async def generate_content_stream(self, **kwargs: Any) -> Any:
        self.configs.append(kwargs["config"])

        async def _iter() -> Any:
            return
            yield
        return _iter()

    async def generate_content(self, **kwargs: Any) -> Any:
        self.configs.append(kwargs["config"])
        return SimpleNamespace(candidates=[], usage_metadata=None)


def _gemini(effort: str) -> tuple[GeminiProvider, _Models]:
    provider = GeminiProvider(ProviderConfig(provider=ProviderName.GEMINI, model="gemini-3.6-flash", reasoning_effort=effort))
    models = _Models()
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=models))  # type: ignore[assignment]
    return provider, models


@pytest.mark.parametrize("effort, expected", [("low", "LOW"), ("HIGH", "HIGH"), ("minimal", "MINIMAL")])
async def test_gemini_sets_the_thinking_level(effort: str, expected: str) -> None:
    provider, models = _gemini(effort)
    _ = [event async for event in provider.stream("sys", [{"role": "user", "content": "q"}], [])]
    await provider.send("sys", [{"role": "user", "content": "q"}], [])
    assert [str(c.thinking_config.thinking_level.value) for c in models.configs] == [expected, expected]


async def test_gemini_without_effort_sends_no_thinking_configuration() -> None:
    provider, models = _gemini("")
    _ = [event async for event in provider.stream("sys", [{"role": "user", "content": "q"}], [])]
    assert models.configs[0].thinking_config is None


async def test_gemini_rejects_an_unknown_level_as_a_provider_error() -> None:
    provider, models = _gemini("xhigh")
    events = [event async for event in provider.stream("sys", [{"role": "user", "content": "q"}], [])]
    assert [e.type for e in events] == ["error"]
    assert "not a Gemini thinking level" in events[0].error
    result = await provider.send("sys", [{"role": "user", "content": "q"}], [])
    assert result["is_error"] and "not a Gemini thinking level" in result["content"]
    assert models.configs == []


def test_the_factory_and_settings_carry_the_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = create_provider(provider="openai", model="gpt-5.6", api_key="k", reasoning_effort="medium")
    assert provider.config.reasoning_effort == "medium"
    assert create_provider(provider="openai", model="gpt-5.6", api_key="k").config.reasoning_effort == ""
