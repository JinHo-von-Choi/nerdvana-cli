"""Tests for Moonshot AI (Kimi) provider integration."""
from __future__ import annotations

import pytest

from nerdvana_cli.providers.base import (
    DEFAULT_BASE_URLS,
    DEFAULT_MODELS,
    PROVIDER_CAPABILITIES,
    PROVIDER_KEY_ENVVARS,
    ProviderName,
    detect_provider,
)
from nerdvana_cli.providers.factory import create_provider
from nerdvana_cli.providers.openai_provider import OpenAIProvider


class TestMoonshotProvider:
    """Moonshot AI provider constants and detection."""

    def test_moonshot_in_provider_name_enum(self) -> None:
        assert ProviderName.MOONSHOT.value == "moonshot"

    def test_moonshot_capabilities(self) -> None:
        caps = PROVIDER_CAPABILITIES[ProviderName.MOONSHOT]
        assert isinstance(caps["supports_tools"], bool)
        assert isinstance(caps["supports_streaming"], bool)
        assert isinstance(caps["supports_vision"], bool)
        assert isinstance(caps["supports_thinking"], bool)
        assert isinstance(caps["max_context"], int)
        assert caps["max_context"] > 0

    def test_moonshot_base_url(self) -> None:
        url = DEFAULT_BASE_URLS[ProviderName.MOONSHOT]
        assert "moonshot.ai" in url
        assert url.endswith("/v1")

    def test_moonshot_default_model(self) -> None:
        assert DEFAULT_MODELS[ProviderName.MOONSHOT] == "kimi-k2-instruct"

    def test_moonshot_api_key_env_vars(self) -> None:
        env_vars = PROVIDER_KEY_ENVVARS[ProviderName.MOONSHOT]
        assert "MOONSHOT_API_KEY" in env_vars
        assert "KIMI_API_KEY" in env_vars

    def test_moonshot_primary_env_var_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MOONSHOT_API_KEY", "primary-key")
        monkeypatch.setenv("KIMI_API_KEY", "fallback-key")
        env_vars = PROVIDER_KEY_ENVVARS[ProviderName.MOONSHOT]
        assert env_vars[0] == "MOONSHOT_API_KEY"

    def test_detect_kimi_prefix(self) -> None:
        assert detect_provider("kimi-k2-instruct") == ProviderName.MOONSHOT
        assert detect_provider("kimi-latest") == ProviderName.MOONSHOT

    def test_detect_moonshot_prefix(self) -> None:
        assert detect_provider("moonshot-v1-128k") == ProviderName.MOONSHOT

    def test_groq_routing_preserved(self) -> None:
        assert detect_provider("llama-3.3-70b-versatile") == ProviderName.GROQ

    def test_create_provider_returns_openai_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MOONSHOT_API_KEY", "test-key")
        instance = create_provider(provider="moonshot")
        assert isinstance(instance, OpenAIProvider)
