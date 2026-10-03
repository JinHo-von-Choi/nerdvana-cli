"""Tools sent with a reasoning effort to OpenAI's chat endpoint are warned about once and explained when refused.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from nerdvana_cli.providers.base import ProviderConfig, ProviderName
from nerdvana_cli.providers.openai_provider import OpenAIProvider, is_official_openai

TOOL = SimpleNamespace(name="Grep", description_text="search", input_schema={"type": "object", "properties": {}})


def _provider(effort: str, provider: ProviderName = ProviderName.OPENAI, base_url: str = "https://api.openai.com/v1") -> OpenAIProvider:
    return OpenAIProvider(ProviderConfig(provider=provider, model="gpt-5.6", api_key="k", base_url=base_url, reasoning_effort=effort))


def _refusal() -> Exception:
    request  = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(400, request=request)
    return openai.BadRequestError("Function tools with reasoning_effort are not supported", response=response, body=None)


def _wire(provider: OpenAIProvider, error: Exception | None = None) -> None:
    async def _empty() -> Any:
        return
        yield

    create = AsyncMock(side_effect=error) if error else AsyncMock(return_value=_empty())
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))  # type: ignore[assignment]


async def _run(provider: OpenAIProvider, tools: list[Any], error: Exception | None = None) -> list[Any]:
    _wire(provider, error)
    return [event async for event in provider.stream("sys", [{"role": "user", "content": "q"}], tools)]


@pytest.mark.parametrize("effort", ["low", "high", "xhigh"])
async def test_tools_with_an_effort_warn_once_per_provider(effort: str, caplog: pytest.LogCaptureFixture) -> None:
    provider = _provider(effort)
    with caplog.at_level(logging.WARNING, logger="nerdvana_cli.providers.openai_provider"):
        await _run(provider, [TOOL])
        await _run(provider, [TOOL])
    warnings = [r for r in caplog.records if "reasoning_effort" in r.getMessage()]
    assert len(warnings) == 1
    assert effort in warnings[0].getMessage()


@pytest.mark.parametrize("effort", ["", "none", "NONE"])
async def test_no_effort_or_none_does_not_warn(effort: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="nerdvana_cli.providers.openai_provider"):
        await _run(_provider(effort), [TOOL])
    assert caplog.records == []


async def test_a_request_without_tools_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="nerdvana_cli.providers.openai_provider"):
        await _run(_provider("high"), [])
    assert caplog.records == []


async def test_other_endpoints_are_not_warned_about(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="nerdvana_cli.providers.openai_provider"):
        await _run(_provider("high", ProviderName.GROQ, "https://api.groq.com/openai/v1"), [TOOL])
        await _run(_provider("high", ProviderName.OPENAI, "http://localhost:11434/v1"), [TOOL])
    assert caplog.records == []


async def test_a_refused_request_carries_the_explanation() -> None:
    events = await _run(_provider("high"), [TOOL], error=_refusal())
    assert events[-1].type == "error"
    assert "not supported" in events[-1].error
    assert "supports tool calling only with reasoning_effort 'none'" in events[-1].error
    assert events[-1].status_code == 400


async def test_a_refusal_without_the_conflict_is_left_alone() -> None:
    events = await _run(_provider(""), [TOOL], error=_refusal())
    assert events[-1].error == "Function tools with reasoning_effort are not supported"


async def test_a_failure_that_is_not_a_refusal_is_left_alone() -> None:
    events = await _run(_provider("high"), [TOOL], error=RuntimeError("connection reset"))
    assert events[-1].error == "connection reset"


async def test_send_carries_the_explanation_too() -> None:
    provider = _provider("high")
    _wire(provider, _refusal())
    result = await provider.send("sys", [{"role": "user", "content": "q"}], [TOOL])
    assert result["is_error"] is True
    assert "supports tool calling only with reasoning_effort 'none'" in result["content"]


def test_official_endpoint_detection() -> None:
    assert is_official_openai(_provider("", base_url="").config)
    assert is_official_openai(_provider("", base_url="https://api.openai.com/v1/").config)
    assert not is_official_openai(_provider("", base_url="http://localhost:11434/v1").config)
    assert not is_official_openai(_provider("", ProviderName.GROQ, "https://api.openai.com/v1").config)
