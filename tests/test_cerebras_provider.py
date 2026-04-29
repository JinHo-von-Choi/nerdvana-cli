"""Tests for Cerebras provider integration."""
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


class TestCerebrasProvider:
    """Cerebras provider constants and detection."""

    def test_cerebras_in_provider_name_enum(self) -> None:
        assert ProviderName.CEREBRAS.value == "cerebras"

    def test_cerebras_capabilities_field_types(self) -> None:
        caps = PROVIDER_CAPABILITIES[ProviderName.CEREBRAS]
        assert isinstance(caps["supports_tools"], bool)
        assert isinstance(caps["supports_streaming"], bool)
        assert isinstance(caps["supports_vision"], bool)
        assert isinstance(caps["supports_thinking"], bool)
        assert isinstance(caps["max_context"], int)
        assert caps["max_context"] > 0

    def test_cerebras_capabilities_values(self) -> None:
        caps = PROVIDER_CAPABILITIES[ProviderName.CEREBRAS]
        assert caps["supports_tools"] is True
        assert caps["supports_streaming"] is True
        assert caps["supports_vision"] is False
        assert caps["supports_thinking"] is False
        assert caps["max_context"] == 65_536

    def test_cerebras_base_url(self) -> None:
        url = DEFAULT_BASE_URLS[ProviderName.CEREBRAS]
        assert "cerebras" in url

    def test_cerebras_default_model(self) -> None:
        assert DEFAULT_MODELS[ProviderName.CEREBRAS] == "llama-3.3-70b"

    def test_cerebras_default_model_not_empty(self) -> None:
        model = DEFAULT_MODELS[ProviderName.CEREBRAS]
        assert model
        assert isinstance(model, str)

    def test_cerebras_api_key_env_vars(self) -> None:
        env_vars = PROVIDER_KEY_ENVVARS[ProviderName.CEREBRAS]
        assert "CEREBRAS_API_KEY" in env_vars

    def test_cerebras_primary_env_var_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CEREBRAS_API_KEY", "primary-key")
        env_vars = PROVIDER_KEY_ENVVARS[ProviderName.CEREBRAS]
        assert env_vars[0] == "CEREBRAS_API_KEY"

    def test_detect_llama_3_3_70b(self) -> None:
        assert detect_provider("llama-3.3-70b") == ProviderName.CEREBRAS

    def test_detect_llama_3_1_8b(self) -> None:
        assert detect_provider("llama-3.1-8b") == ProviderName.CEREBRAS

    def test_detect_qwen_3_32b(self) -> None:
        assert detect_provider("qwen-3-32b") == ProviderName.CEREBRAS

    def test_groq_routing_preserved(self) -> None:
        assert detect_provider("llama-3.3-70b-versatile") == ProviderName.GROQ

    def test_cerebras_whitelist_excludes_groq_variants(self) -> None:
        assert detect_provider("llama-3.3-70b-versatile") != ProviderName.CEREBRAS

    def test_create_provider_returns_openai_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CEREBRAS_API_KEY", "test-key")
        instance = create_provider(provider="cerebras")
        assert isinstance(instance, OpenAIProvider)
