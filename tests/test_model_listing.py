"""Tests for dynamic model listing."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nerdvana_cli.providers.base import ModelInfo, ProviderConfig, ProviderName
from nerdvana_cli.providers.openai_provider import OpenAIProvider


class TestOpenAIListModels:
    @pytest.mark.asyncio
    async def test_list_models_returns_model_info(self):
        config = ProviderConfig(
            provider=ProviderName.DEEPSEEK,
            api_key="test-key",
            base_url="https://api.deepseek.com",
            model="deepseek-chat",
        )
        provider = OpenAIProvider(config)

        mock_model_1 = MagicMock()
        mock_model_1.id = "deepseek-chat"
        mock_model_1.created = 1700000000
        mock_model_2 = MagicMock()
        mock_model_2.id = "deepseek-reasoner"
        mock_model_2.created = 1700000001

        mock_page = MagicMock()
        mock_page.data = [mock_model_1, mock_model_2]

        mock_client = AsyncMock()
        mock_client.models.list = AsyncMock(return_value=mock_page)

        with patch.object(provider, "_get_client", return_value=mock_client):
            models = await provider.list_models()

        assert len(models) == 2
        assert isinstance(models[0], ModelInfo)
        assert models[0].id == "deepseek-chat"
        assert models[1].id == "deepseek-reasoner"

    @pytest.mark.asyncio
    async def test_list_models_error_returns_empty(self):
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
