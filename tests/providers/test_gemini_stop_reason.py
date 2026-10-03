"""GeminiProvider on generateContent reads the finish reason: a reply cut off at the output limit is ``max_tokens``.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import types

from nerdvana_cli.providers.base import ProviderConfig, ProviderName
from nerdvana_cli.providers.gemini_provider import GeminiProvider


def _candidate(finish: Any, text: str | None = "partial", call: str | None = None) -> SimpleNamespace:
    part = SimpleNamespace(text=text, function_call=SimpleNamespace(name=call, args={}) if call else None, thought_signature=None)
    return SimpleNamespace(content=SimpleNamespace(parts=[part]), finish_reason=finish)


class _Models:
    def __init__(self, chunks: list[Any]) -> None:
        self.chunks = chunks

    async def generate_content_stream(self, **kwargs: Any) -> Any:
        async def _iter() -> Any:
            for chunk in self.chunks:
                yield chunk
        return _iter()

    async def generate_content(self, **kwargs: Any) -> Any:
        return SimpleNamespace(candidates=self.chunks[-1].candidates, usage_metadata=None)


def _provider(chunks: list[Any]) -> GeminiProvider:
    provider = GeminiProvider(ProviderConfig(provider=ProviderName.GEMINI, model="gemini-3.6-flash"))
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=_Models(chunks)))  # type: ignore[assignment]
    return provider


def _chunk(*candidates: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(candidates=list(candidates), usage_metadata=None)


async def _stop(chunks: list[Any]) -> str:
    events = [e async for e in _provider(chunks).stream("s", [{"role": "user", "content": "go"}], [])]
    return events[-1].stop_reason


@pytest.mark.parametrize(("finish", "expected"), [
    (types.FinishReason.STOP,       "end_turn"),
    (types.FinishReason.MAX_TOKENS, "max_tokens"),
    (types.FinishReason.SAFETY,     "end_turn"),
    (None,                          "end_turn"),
])
async def test_a_streamed_reply_ends_as_its_finish_reason_says(finish: Any, expected: str) -> None:
    assert await _stop([_chunk(_candidate(None)), _chunk(_candidate(finish, text=None))]) == expected


async def test_the_reason_is_read_whichever_chunk_carries_it() -> None:
    assert await _stop([_chunk(_candidate(types.FinishReason.MAX_TOKENS)), _chunk(_candidate(None, text="more"))]) == "max_tokens"


async def test_a_reply_with_calls_is_tool_use_even_when_the_limit_was_hit() -> None:
    assert await _stop([_chunk(_candidate(types.FinishReason.MAX_TOKENS, text=None, call="Grep"))]) == "tool_use"


@pytest.mark.parametrize(("finish", "expected"), [(types.FinishReason.STOP, "end_turn"), (types.FinishReason.MAX_TOKENS, "max_tokens")])
async def test_send_reads_the_finish_reason_too(finish: Any, expected: str) -> None:
    result = await _provider([_chunk(_candidate(finish))]).send("s", [{"role": "user", "content": "go"}], [])
    assert result["stop_reason"] == expected


async def test_send_reports_tool_use_for_a_call() -> None:
    result = await _provider([_chunk(_candidate(types.FinishReason.STOP, text=None, call="Grep"))]).send("s", [{"role": "user", "content": "go"}], [])
    assert result["stop_reason"] == "tool_use"
