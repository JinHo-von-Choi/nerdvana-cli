"""Regression tests for /model provider/base_url preservation.

Guards against a real user bug:
1. User switched to Ollama via /provider (sets provider="ollama", base_url=localhost).
2. User picked "gemma4:31b-cloud" from the Ollama model list.
3. /model handler previously re-detected provider from the model ID, which
   failed to match any known prefix and silently fell back to Anthropic.
4. Result: provider=anthropic, base_url=http://localhost:11434/v1 — a mixed
   state that routed Anthropic SDK traffic at Ollama's Go HTTP server.

The fix has two parts:
- handle_model must NEVER re-detect or change provider/base_url.
- detect_provider must recognize Ollama's distinctive tag-separated naming
  (``name:tag``) and ``-cloud`` suffix, instead of falling through to the
  Anthropic default.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nerdvana_cli.commands.model_commands import handle_model
from nerdvana_cli.providers.base import ProviderName, detect_provider


class TestDetectProviderOllama:
    """detect_provider must route Ollama tag names to ProviderName.OLLAMA."""

    def test_gemma4_cloud_tag_returns_ollama(self):
        assert detect_provider("gemma4:31b-cloud") == ProviderName.OLLAMA

    def test_qwen3_latest_returns_ollama(self):
        assert detect_provider("qwen3:latest") == ProviderName.OLLAMA

    def test_llama3_70b_tag_returns_ollama(self):
        assert detect_provider("llama3:70b") == ProviderName.OLLAMA

    def test_cloud_suffix_returns_ollama(self):
        assert detect_provider("mymodel-cloud") == ProviderName.OLLAMA

    def test_case_insensitive_tag(self):
        assert detect_provider("Qwen3:LATEST") == ProviderName.OLLAMA


class TestDetectProviderNoRegression:
    """Known prefixes must still route to their original providers."""

    def test_claude_still_anthropic(self):
        assert detect_provider("claude-sonnet-4-6") == ProviderName.ANTHROPIC

    def test_gpt_still_openai(self):
        assert detect_provider("gpt-4o") == ProviderName.OPENAI

    def test_gemini_still_gemini(self):
        assert detect_provider("gemini-2.5-pro") == ProviderName.GEMINI

    def test_deepseek_still_deepseek(self):
        assert detect_provider("deepseek-chat") == ProviderName.DEEPSEEK


def _make_mock_app(provider: str, model: str, base_url: str) -> MagicMock:
    """Build a minimal NerdvanaApp stub that exercises handle_model end to end.

    Ground truth lives in ``app.settings.model.*``: the test asserts directly
    on those fields after ``handle_model`` returns, which is strictly stronger
    than spying on ``detect_provider`` — it catches any future reintroduction
    of provider mutation no matter how it is spelled.
    """
    model_cfg = SimpleNamespace(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key="test-key",
    )
    settings = SimpleNamespace(model=model_cfg)

    agent_loop = MagicMock()
    agent_loop.create_provider_from_settings = MagicMock(return_value=MagicMock())
    agent_loop.registry = MagicMock()
    agent_loop.registry.all_tools = MagicMock(return_value=[])

    app = MagicMock()
    app.settings = settings
    app._agent_loop = agent_loop
    app.parism_client = None
    app.query_one = MagicMock(return_value=MagicMock())
    app._add_chat_message = MagicMock()
    app._update_banner = MagicMock()
    return app


@pytest.mark.asyncio
async def test_handle_model_preserves_provider_and_base_url():
    """Repro of the real user bug.

    Given a fully configured Ollama session, ``/model gemma4:31b-cloud`` must:
    - update ``settings.model.model`` to the new id,
    - leave ``settings.model.provider`` as "ollama" (NOT re-detect),
    - leave ``settings.model.base_url`` untouched,
    - rebuild the AgentLoop provider instance via ``create_provider_from_settings``
      so the active client picks up the new model id.
    """
    app = _make_mock_app(
        provider="ollama",
        model="qwen3",
        base_url="http://localhost:11434/v1",
    )

    await handle_model(app, "gemma4:31b-cloud")

    assert app.settings.model.provider == "ollama"
    assert app.settings.model.model == "gemma4:31b-cloud"
    assert app.settings.model.base_url == "http://localhost:11434/v1"
    assert app._agent_loop.create_provider_from_settings.called is True


@pytest.mark.asyncio
async def test_handle_model_preserves_anthropic_state_across_model_swap():
    """Inverse case: swapping models inside Anthropic must not touch base_url."""
    app = _make_mock_app(
        provider="anthropic",
        model="claude-sonnet-4-6",
        base_url="",
    )

    await handle_model(app, "claude-opus-4")

    assert app.settings.model.provider == "anthropic"
    assert app.settings.model.model == "claude-opus-4"
    assert app.settings.model.base_url == ""
    assert app._agent_loop.create_provider_from_settings.called is True
