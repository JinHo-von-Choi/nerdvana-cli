"""Tests for Featherless AI provider integration."""
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


class TestFeatherlessProvider:
    """Featherless AI provider constants and detection."""

    def test_featherless_in_provider_name_enum(self):
        assert ProviderName.FEATHERLESS == "featherless"

    def test_featherless_capabilities_defined(self):
        caps = PROVIDER_CAPABILITIES[ProviderName.FEATHERLESS]
        assert isinstance(caps["supports_tools"], bool)
        assert isinstance(caps["supports_streaming"], bool)
        assert isinstance(caps["max_context"], int)
        assert caps["max_context"] > 0

    def test_featherless_base_url(self):
        url = DEFAULT_BASE_URLS[ProviderName.FEATHERLESS]
        assert "featherless.ai" in url
        assert url.endswith("/v1")

    def test_featherless_default_model(self):
        model = DEFAULT_MODELS[ProviderName.FEATHERLESS]
        assert model
        assert isinstance(model, str)

    def test_featherless_api_key_env_var(self):
        env_vars = PROVIDER_KEY_ENVVARS[ProviderName.FEATHERLESS]
        assert "FEATHERLESS_API_KEY" in env_vars

    def test_detect_featherless_prefix(self):
        assert detect_provider("featherless-llama-3-70b") == ProviderName.FEATHERLESS
        assert detect_provider("featherless-qwen-7b") == ProviderName.FEATHERLESS
