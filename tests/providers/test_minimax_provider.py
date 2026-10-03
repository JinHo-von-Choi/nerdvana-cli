"""Tests for MiniMax provider integration."""
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


class TestMiniMaxProvider:
    """MiniMax provider constants and detection."""

    def test_minimax_in_provider_name_enum(self) -> None:
        assert ProviderName.MINIMAX.value == "minimax"

    def test_minimax_capabilities_field_types(self) -> None:
        caps = PROVIDER_CAPABILITIES[ProviderName.MINIMAX]
        assert isinstance(caps["supports_tools"], bool)
        assert isinstance(caps["supports_streaming"], bool)
        assert isinstance(caps["supports_vision"], bool)
        assert isinstance(caps["supports_thinking"], bool)
        assert isinstance(caps["max_context"], int)
        assert caps["max_context"] > 0

    def test_minimax_capabilities_values(self) -> None:
        caps = PROVIDER_CAPABILITIES[ProviderName.MINIMAX]
        assert caps["supports_tools"] is True
        assert caps["supports_streaming"] is True
        assert caps["supports_vision"] is True
        assert caps["supports_thinking"] is False
        assert caps["max_context"] == 1_000_000

    def test_minimax_base_url(self) -> None:
        url = DEFAULT_BASE_URLS[ProviderName.MINIMAX]
        assert "minimaxi" in url

    def test_minimax_default_model(self) -> None:
        assert DEFAULT_MODELS[ProviderName.MINIMAX] == "MiniMax-M2"

    def test_minimax_default_model_not_empty(self) -> None:
        model = DEFAULT_MODELS[ProviderName.MINIMAX]
        assert model
        assert isinstance(model, str)

    def test_minimax_api_key_env_vars(self) -> None:
        env_vars = PROVIDER_KEY_ENVVARS[ProviderName.MINIMAX]
        assert "MINIMAX_API_KEY" in env_vars

    def test_minimax_primary_env_var_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINIMAX_API_KEY", "primary-key")
        env_vars = PROVIDER_KEY_ENVVARS[ProviderName.MINIMAX]
        assert env_vars[0] == "MINIMAX_API_KEY"

    def test_detect_minimax_uppercase_prefix(self) -> None:
        assert detect_provider("MiniMax-M2") == ProviderName.MINIMAX

    def test_detect_minimax_lowercase_prefix(self) -> None:
        assert detect_provider("minimax-m2") == ProviderName.MINIMAX

    def test_detect_abab_prefix(self) -> None:
        assert detect_provider("abab6.5s-chat") == ProviderName.MINIMAX

    def test_groq_routing_preserved(self) -> None:
        assert detect_provider("llama-3.3-70b-versatile") == ProviderName.GROQ

    def test_create_provider_returns_openai_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
        instance = create_provider(provider="minimax")
        assert isinstance(instance, OpenAIProvider)
