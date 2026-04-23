"""Tests for Xiaomi MiMo provider integration."""
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


class TestXiaomiMimoProvider:
    """Xiaomi MiMo provider constants and detection."""

    def test_xiaomi_mimo_in_provider_name_enum(self):
        assert ProviderName.XIAOMI_MIMO == "xiaomi_mimo"

    def test_xiaomi_mimo_capabilities(self):
        caps = PROVIDER_CAPABILITIES[ProviderName.XIAOMI_MIMO]
        assert caps["supports_tools"] is True
        assert caps["supports_streaming"] is True
        assert caps["supports_vision"] is True
        assert caps["supports_thinking"] is True
        assert caps["max_context"] == 1_048_576

    def test_xiaomi_mimo_base_url(self):
        url = DEFAULT_BASE_URLS[ProviderName.XIAOMI_MIMO]
        assert "xiaomimimo.com" in url
        assert url.endswith("/v1")

    def test_xiaomi_mimo_default_model(self):
        assert DEFAULT_MODELS[ProviderName.XIAOMI_MIMO] == "mimo-v2.5-pro"

    def test_xiaomi_mimo_api_key_env_vars(self):
        env_vars = PROVIDER_KEY_ENVVARS[ProviderName.XIAOMI_MIMO]
        assert "MIMO_API_KEY" in env_vars
        assert "XIAOMI_API_KEY" in env_vars

    def test_detect_xiaomi_mimo_prefix(self):
        assert detect_provider("mimo-v2.5-pro") == ProviderName.XIAOMI_MIMO
        assert detect_provider("mimo-v2-omni") == ProviderName.XIAOMI_MIMO
        assert detect_provider("mimo-v2-flash") == ProviderName.XIAOMI_MIMO
        assert detect_provider("mimo-v2-pro") == ProviderName.XIAOMI_MIMO
