"""Tests for MiniMax provider integration."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nerdvana_cli.core.telemetry.analytics.pricing import PricingTable
from nerdvana_cli.providers.base import (
    DEFAULT_BASE_URLS,
    DEFAULT_MODELS,
    PROVIDER_CAPABILITIES,
    PROVIDER_KEY_ENVVARS,
    ModelInfo,
    ProviderConfig,
    ProviderName,
    detect_provider,
    resolve_context_window,
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
        assert DEFAULT_MODELS[ProviderName.MINIMAX] == "MiniMax-M3.1-Flash-Preview"

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


class TestMiniMaxListModels:
    """MiniMax model listing merges the list the CLI ships."""

    @staticmethod
    def _provider() -> OpenAIProvider:
        config = ProviderConfig(
            provider=ProviderName.MINIMAX,
            api_key="test-key",
            base_url="https://api.minimaxi.chat/v1",
            model="MiniMax-M3.1-Flash-Preview",
        )
        return OpenAIProvider(config)

    @pytest.mark.asyncio
    async def test_api_failure_returns_shipped_models(self) -> None:
        provider = self._provider()
        mock_client = AsyncMock()
        mock_client.models.list = AsyncMock(side_effect=Exception("auth error"))

        with patch.object(provider, "_get_client", return_value=mock_client):
            models = await provider.list_models()

        ids = [m.id for m in models]
        assert all(isinstance(m, ModelInfo) for m in models)
        assert "MiniMax-M3.1-Flash-Preview" in ids
        assert {"MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2"} <= set(ids)
        assert ids == sorted(ids)

    @pytest.mark.asyncio
    async def test_api_response_missing_current_models_gets_merged(self) -> None:
        provider = self._provider()
        mock_model = MagicMock()
        mock_model.id = "MiniMax-M2"
        mock_model.created = 1700000000
        mock_page = MagicMock()
        mock_page.data = [mock_model]
        mock_client = AsyncMock()
        mock_client.models.list = AsyncMock(return_value=mock_page)

        with patch.object(provider, "_get_client", return_value=mock_client):
            models = await provider.list_models()

        ids = [m.id for m in models]
        assert "MiniMax-M3.1-Flash-Preview" in ids
        assert ids.count("MiniMax-M2") == 1
        assert ids == sorted(ids)

    @pytest.mark.asyncio
    async def test_non_minimax_provider_keeps_empty_result(self) -> None:
        config = ProviderConfig(
            provider=ProviderName.OPENAI,
            api_key="bad-key",
            base_url="https://api.openai.com/v1",
        )
        provider = OpenAIProvider(config)
        mock_client = AsyncMock()
        mock_client.models.list = AsyncMock(side_effect=Exception("auth error"))

        with patch.object(provider, "_get_client", return_value=mock_client):
            models = await provider.list_models()

        assert models == []


class TestMiniMaxM31Entries:
    """MiniMax M3.1 pricing rates and context windows shipped with the CLI."""

    def test_pricing_entry_present_for_m31_models(self) -> None:
        table = PricingTable()
        assert table.has_price("minimax", "MiniMax-M3.1-Flash-Preview")
        assert table.has_price("minimax", "MiniMax-M3.1")

    def test_pricing_matches_minimax_rate_card(self) -> None:
        table = PricingTable()
        model = "MiniMax-M3.1-Flash-Preview"
        # 1M input at $0.30/1M + 1M output at $1.20/1M
        assert table.estimate_cost("minimax", model, 1_000_000, 1_000_000) == pytest.approx(1.5)
        # fully cached prompt is billed at the cache-read rate
        assert table.estimate_cost("minimax", model, 1_000_000, 0, cache_read_tokens=1_000_000) == pytest.approx(0.06)

    def test_unknown_minimax_model_still_costless(self) -> None:
        table = PricingTable()
        assert table.estimate_cost("minimax", "MiniMax-Not-A-Model", 1_000_000, 1_000_000) == 0.0

    @pytest.mark.parametrize("model", ["MiniMax-M3.1-Flash-Preview", "MiniMax-M3.1", "MiniMax-M3"])
    def test_context_window_is_one_million(self, model: str) -> None:
        assert resolve_context_window(ProviderName.MINIMAX, model) == 1_000_000
