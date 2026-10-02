"""Live smoke for anthropic provider — invoked only when API key env is set."""

from __future__ import annotations

import asyncio

import pytest

from nerdvana_cli.providers.factory import create_provider
from tests.live.conftest import LIVE_TIMEOUT, MAX_LIVE_TOKENS


@pytest.mark.live
def test_smoke_anthropic() -> None:
    """Round-trip a 'reply with OK' prompt and verify a non-empty bounded response."""

    async def _call() -> None:
        provider = create_provider(provider="anthropic", max_tokens=MAX_LIVE_TOKENS)
        response = await asyncio.wait_for(
            provider.send(
                system_prompt="Reply with the single word OK.",
                messages=[{"role": "user", "content": "ping"}],
                tools=[],
            ),
            timeout=LIVE_TIMEOUT,
        )
        assert response, "empty response payload"
        content = (
            response.get("content") if isinstance(response, dict)
            else getattr(response, "content", None)
        )
        assert content, f"no content in response: {response!r}"

    asyncio.run(_call())


class _EchoSpec:
    name             = "echo"
    description_text = "Echo the given text back."
    input_schema     = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}


@pytest.mark.live
def test_smoke_anthropic_tool_round_trip() -> None:
    """A tool call and its result survive the history conversion on the next request."""

    async def _call() -> None:
        provider = create_provider(provider="anthropic", max_tokens=200)
        history: list[dict[str, object]] = [
            {"role": "user", "content": "Call the echo tool with the text hello, then stop."},
        ]
        first = await asyncio.wait_for(
            provider.send(system_prompt="Use the tool when asked.", messages=history, tools=[_EchoSpec()]),
            timeout=LIVE_TIMEOUT,
        )
        tool_uses = first.get("tool_uses") or []
        assert tool_uses, f"model did not call the tool: {first!r}"
        history.append({"role": "assistant", "content": first.get("content", ""), "tool_uses": tool_uses})
        history.append({"role": "tool", "content": "hello", "tool_use_id": tool_uses[0]["id"]})
        second = await asyncio.wait_for(
            provider.send(system_prompt="Use the tool when asked.", messages=history, tools=[_EchoSpec()]),
            timeout=LIVE_TIMEOUT,
        )
        assert not second.get("is_error"), f"second request rejected: {second!r}"

    asyncio.run(_call())
