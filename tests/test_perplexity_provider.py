"""Tests for Perplexity AI provider integration."""
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


class TestPerplexityProvider:
    """Perplexity AI provider constants and detection."""

    def test_perplexity_in_provider_name_enum(self) -> None:
        assert ProviderName.PERPLEXITY.value == "perplexity"

    def test_perplexity_capabilities_field_types(self) -> None:
        caps = PROVIDER_CAPABILITIES[ProviderName.PERPLEXITY]
        assert isinstance(caps["supports_tools"], bool)
        assert isinstance(caps["supports_streaming"], bool)
        assert isinstance(caps["supports_vision"], bool)
        assert isinstance(caps["supports_thinking"], bool)
        assert isinstance(caps["max_context"], int)
        assert caps["max_context"] > 0

    def test_perplexity_capabilities_values(self) -> None:
        caps = PROVIDER_CAPABILITIES[ProviderName.PERPLEXITY]
        assert caps["supports_tools"] is False
        assert caps["supports_streaming"] is True
        assert caps["supports_vision"] is False
        assert caps["supports_thinking"] is False
        assert caps["max_context"] == 200_000

    def test_perplexity_base_url(self) -> None:
        url = DEFAULT_BASE_URLS[ProviderName.PERPLEXITY]
        assert "perplexity" in url

    def test_perplexity_default_model(self) -> None:
        assert DEFAULT_MODELS[ProviderName.PERPLEXITY] == "sonar-pro"

    def test_perplexity_default_model_not_empty(self) -> None:
        model = DEFAULT_MODELS[ProviderName.PERPLEXITY]
        assert model
        assert isinstance(model, str)

    def test_perplexity_api_key_env_vars(self) -> None:
        env_vars = PROVIDER_KEY_ENVVARS[ProviderName.PERPLEXITY]
        assert "PERPLEXITY_API_KEY" in env_vars
        assert "PPLX_API_KEY" in env_vars

    def test_perplexity_primary_env_var_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERPLEXITY_API_KEY", "primary-key")
        monkeypatch.setenv("PPLX_API_KEY", "fallback-key")
        env_vars = PROVIDER_KEY_ENVVARS[ProviderName.PERPLEXITY]
        assert env_vars[0] == "PERPLEXITY_API_KEY"

    def test_perplexity_fallback_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
        monkeypatch.setenv("PPLX_API_KEY", "fallback-key")
        env_vars = PROVIDER_KEY_ENVVARS[ProviderName.PERPLEXITY]
        assert "PPLX_API_KEY" in env_vars

    def test_detect_sonar_pro(self) -> None:
        assert detect_provider("sonar-pro") == ProviderName.PERPLEXITY

    def test_detect_sonar_reasoning_pro(self) -> None:
        assert detect_provider("sonar-reasoning-pro") == ProviderName.PERPLEXITY

    def test_detect_pplx_prefix(self) -> None:
        assert detect_provider("pplx-7b-online") == ProviderName.PERPLEXITY

    def test_groq_routing_preserved(self) -> None:
        assert detect_provider("llama-3.3-70b-versatile") == ProviderName.GROQ

    def test_create_provider_returns_openai_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
        instance = create_provider(provider="perplexity")
        assert isinstance(instance, OpenAIProvider)
