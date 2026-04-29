"""Tests for Fireworks AI provider integration."""
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


class TestFireworksProvider:
    """Fireworks AI provider constants and detection."""

    def test_fireworks_in_provider_name_enum(self) -> None:
        assert ProviderName.FIREWORKS.value == "fireworks"

    def test_fireworks_capabilities_field_types(self) -> None:
        caps = PROVIDER_CAPABILITIES[ProviderName.FIREWORKS]
        assert isinstance(caps["supports_tools"], bool)
        assert isinstance(caps["supports_streaming"], bool)
        assert isinstance(caps["supports_vision"], bool)
        assert isinstance(caps["supports_thinking"], bool)
        assert isinstance(caps["max_context"], int)
        assert caps["max_context"] > 0

    def test_fireworks_capabilities_values(self) -> None:
        caps = PROVIDER_CAPABILITIES[ProviderName.FIREWORKS]
        assert caps["supports_tools"] is True
        assert caps["supports_streaming"] is True
        assert caps["supports_vision"] is False
        assert caps["supports_thinking"] is False
        assert caps["max_context"] == 131_072

    def test_fireworks_base_url(self) -> None:
        url = DEFAULT_BASE_URLS[ProviderName.FIREWORKS]
        assert "fireworks" in url

    def test_fireworks_default_model(self) -> None:
        assert DEFAULT_MODELS[ProviderName.FIREWORKS] == "accounts/fireworks/models/llama-v3p3-70b-instruct"

    def test_fireworks_default_model_not_empty(self) -> None:
        model = DEFAULT_MODELS[ProviderName.FIREWORKS]
        assert model
        assert isinstance(model, str)

    def test_fireworks_api_key_env_vars(self) -> None:
        env_vars = PROVIDER_KEY_ENVVARS[ProviderName.FIREWORKS]
        assert "FIREWORKS_API_KEY" in env_vars

    def test_fireworks_primary_env_var_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FIREWORKS_API_KEY", "primary-key")
        env_vars = PROVIDER_KEY_ENVVARS[ProviderName.FIREWORKS]
        assert env_vars[0] == "FIREWORKS_API_KEY"

    def test_detect_fireworks_default_model(self) -> None:
        assert detect_provider("accounts/fireworks/models/llama-v3p3-70b-instruct") == ProviderName.FIREWORKS

    def test_detect_fireworks_qwen_model(self) -> None:
        assert detect_provider("accounts/fireworks/models/qwen2p5-72b-instruct") == ProviderName.FIREWORKS

    def test_detect_fireworks_prefix_required(self) -> None:
        assert detect_provider("accounts/fireworks/models/mixtral-8x22b-instruct") == ProviderName.FIREWORKS

    def test_groq_routing_preserved(self) -> None:
        assert detect_provider("llama-3.3-70b-versatile") == ProviderName.GROQ

    def test_create_provider_returns_openai_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
        instance = create_provider(provider="fireworks")
        assert isinstance(instance, OpenAIProvider)
