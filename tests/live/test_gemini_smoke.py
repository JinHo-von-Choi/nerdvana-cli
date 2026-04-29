"""Live smoke for gemini provider — invoked only when API key env is set."""

from __future__ import annotations

import asyncio

import pytest

from nerdvana_cli.providers.factory import create_provider
from tests.live.conftest import LIVE_TIMEOUT, MAX_LIVE_TOKENS


@pytest.mark.live
def test_smoke_gemini() -> None:
    """Round-trip a 'reply with OK' prompt and verify a non-empty bounded response."""

    async def _call() -> None:
        provider = create_provider(provider="gemini", max_tokens=MAX_LIVE_TOKENS)
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
