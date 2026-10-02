"""GeminiProvider.stream reports the cumulative usage once, before done.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from nerdvana_cli.providers.base import ProviderConfig, ProviderName
from nerdvana_cli.providers.gemini_provider import GeminiProvider


def _chunk(text: str | None, prompt: int | None = None, candidates: int | None = None) -> SimpleNamespace:
    parts   = [SimpleNamespace(text=text, function_call=None)] if text else []
    usage   = SimpleNamespace(prompt_token_count=prompt, candidates_token_count=candidates) if prompt is not None else None
    content = SimpleNamespace(parts=parts) if parts else None
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)], usage_metadata=usage)


class _Models:
    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self.chunks = chunks

    async def generate_content_stream(self, **kwargs: Any) -> Any:
        async def _iter() -> Any:
            for chunk in self.chunks:
                yield chunk
        return _iter()


def _provider(chunks: list[SimpleNamespace]) -> GeminiProvider:
    provider = GeminiProvider(ProviderConfig(provider=ProviderName.GEMINI, model="gemini-2.5-flash"))
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=_Models(chunks)))  # type: ignore[assignment]
    return provider


async def _events(provider: GeminiProvider) -> list[Any]:
    return [event async for event in provider.stream("sys", [{"role": "user", "content": "hi"}], [])]


async def test_last_reported_counts_are_emitted_once_before_done() -> None:
    events = await _events(_provider([
        _chunk("a", prompt=100, candidates=1),
        _chunk("b", prompt=100, candidates=7),
        _chunk(None),
    ]))
    usage = [e for e in events if e.type == "usage"]
    assert len(usage) == 1
    assert usage[0].usage == {"input_tokens": 100, "output_tokens": 7}
    assert events[-1].type == "done"
    assert events.index(usage[0]) < events.index(events[-1])


async def test_no_usage_event_when_the_server_reports_none() -> None:
    events = await _events(_provider([_chunk("hello")]))
    assert [e.type for e in events] == ["content_delta", "done"]


async def test_missing_counts_default_to_zero() -> None:
    events = await _events(_provider([_chunk("x", prompt=5, candidates=None)]))
    assert [e.usage for e in events if e.type == "usage"] == [{"input_tokens": 5, "output_tokens": 0}]
