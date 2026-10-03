"""Tests for Alibaba DashScope (Qwen) provider integration."""
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


class TestDashscopeProvider:
    """Alibaba DashScope provider constants and detection."""

    def test_dashscope_in_provider_name_enum(self) -> None:
        assert ProviderName.DASHSCOPE.value == "dashscope"

    def test_dashscope_capabilities(self) -> None:
        caps = PROVIDER_CAPABILITIES[ProviderName.DASHSCOPE]
        assert isinstance(caps["supports_tools"], bool)
        assert isinstance(caps["supports_streaming"], bool)
        assert isinstance(caps["supports_vision"], bool)
        assert isinstance(caps["supports_thinking"], bool)
        assert isinstance(caps["max_context"], int)
        assert caps["max_context"] > 0

    def test_dashscope_base_url(self) -> None:
        url = DEFAULT_BASE_URLS[ProviderName.DASHSCOPE]
        assert "dashscope" in url
        assert url.endswith("compatible-mode/v1")

    def test_dashscope_default_model(self) -> None:
        assert DEFAULT_MODELS[ProviderName.DASHSCOPE] == "qwen3-coder-plus"

    def test_dashscope_default_models_not_empty(self) -> None:
        model = DEFAULT_MODELS[ProviderName.DASHSCOPE]
        assert model
        assert isinstance(model, str)

    def test_dashscope_api_key_env_vars(self) -> None:
        env_vars = PROVIDER_KEY_ENVVARS[ProviderName.DASHSCOPE]
        assert "DASHSCOPE_API_KEY" in env_vars
        assert "ALIBABA_API_KEY" in env_vars

    def test_dashscope_primary_env_var_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DASHSCOPE_API_KEY", "primary-key")
        monkeypatch.setenv("ALIBABA_API_KEY", "fallback-key")
        env_vars = PROVIDER_KEY_ENVVARS[ProviderName.DASHSCOPE]
        assert env_vars[0] == "DASHSCOPE_API_KEY"

    def test_detect_qwen_max(self) -> None:
        assert detect_provider("qwen-max") == ProviderName.DASHSCOPE

    def test_detect_qwen_plus(self) -> None:
        assert detect_provider("qwen-plus") == ProviderName.DASHSCOPE

    def test_detect_qwen_turbo(self) -> None:
        assert detect_provider("qwen-turbo") == ProviderName.DASHSCOPE

    def test_detect_qwen_vl_max(self) -> None:
        assert detect_provider("qwen-vl-max") == ProviderName.DASHSCOPE

    def test_detect_qwen3_coder_plus(self) -> None:
        assert detect_provider("qwen3-coder-plus") == ProviderName.DASHSCOPE

    def test_detect_qwq_32b_preview(self) -> None:
        assert detect_provider("qwq-32b-preview") == ProviderName.DASHSCOPE

    def test_groq_routing_preserved(self) -> None:
        assert detect_provider("llama-3.3-70b-versatile") == ProviderName.GROQ

    def test_create_provider_returns_openai_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
        instance = create_provider(provider="dashscope")
        assert isinstance(instance, OpenAIProvider)
