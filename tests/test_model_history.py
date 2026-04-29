"""Tests for M1: model_history persistence in NerdvanaSettings."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(tmp_path, data: dict) -> str:
    p = tmp_path / "nerdvana.yml"
    with open(p, "w") as f:
        yaml.dump(data, f)
    return str(p)


# ---------------------------------------------------------------------------
# M1-1: yaml round-trip preserves model_history
# ---------------------------------------------------------------------------

def test_settings_load_model_history(tmp_path):
    """NerdvanaSettings.load reads model_history from yaml."""
    cfg = _write_config(tmp_path, {
        "model": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
        "model_history": {"anthropic": "claude-opus-4", "openai": "gpt-4o"},
    })

    from nerdvana_cli.core.settings import NerdvanaSettings
    s = NerdvanaSettings.load(config_path=cfg)

    assert s.model_history == {"anthropic": "claude-opus-4", "openai": "gpt-4o"}


def test_settings_load_missing_model_history(tmp_path):
    """model_history defaults to empty dict when key absent from yaml."""
    cfg = _write_config(tmp_path, {
        "model": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
    })

    from nerdvana_cli.core.settings import NerdvanaSettings
    s = NerdvanaSettings.load(config_path=cfg)

    assert s.model_history == {}


def test_settings_default_model_history_is_dict():
    """NerdvanaSettings() without config has model_history as empty dict."""
    from nerdvana_cli.core.settings import NerdvanaSettings
    s = NerdvanaSettings()
    assert isinstance(s.model_history, dict)
    assert s.model_history == {}


# ---------------------------------------------------------------------------
# M1-2: switch_provider restores last_model from history
# ---------------------------------------------------------------------------

def _make_app_mock(model_history: dict, current_provider: str = "anthropic") -> MagicMock:
    """Build a minimal NerdvanaApp mock for switch_provider tests."""
    app = MagicMock()
    app.settings.model.provider = current_provider
    app.settings.model.model = "claude-sonnet-4-20250514"
    app.settings.model.api_key = "test-key"
    app.settings.model.base_url = ""
    app.settings.model.max_tokens = 8192
    app.settings.model.temperature = 1.0
    app.settings.model_history = dict(model_history)
    app._agent_loop.create_provider_from_settings.return_value = MagicMock()
    app._pending_provider = ""
    return app


@pytest.mark.asyncio
async def test_switch_provider_uses_history(tmp_path):
    """switch_provider picks last_model from model_history when entry exists."""
    from nerdvana_cli.commands.model_commands import switch_provider

    app = _make_app_mock(model_history={"dashscope": "qwen-plus"}, current_provider="anthropic")

    with (
        patch("nerdvana_cli.core.setup.load_config", return_value={}),
        patch("nerdvana_cli.core.setup.save_config"),
        patch("nerdvana_cli.providers.factory.create_provider") as mock_cp,
    ):
        mock_provider = AsyncMock()
        mock_provider.list_models = AsyncMock(return_value=[])
        mock_cp.return_value = mock_provider

        # Switch to dashscope — should restore qwen-plus
        await switch_provider(app, "dashscope", "fake-key")

    assert app.settings.model.model == "qwen-plus"


@pytest.mark.asyncio
async def test_switch_provider_falls_back_to_default(tmp_path):
    """switch_provider falls back to DEFAULT_MODELS when history is empty."""
    from nerdvana_cli.commands.model_commands import switch_provider
    from nerdvana_cli.providers.base import DEFAULT_MODELS, ProviderName

    app = _make_app_mock(model_history={}, current_provider="anthropic")

    with (
        patch("nerdvana_cli.core.setup.load_config", return_value={}),
        patch("nerdvana_cli.core.setup.save_config"),
        patch("nerdvana_cli.providers.factory.create_provider") as mock_cp,
    ):
        mock_provider = AsyncMock()
        mock_provider.list_models = AsyncMock(return_value=[])
        mock_cp.return_value = mock_provider

        await switch_provider(app, "openai", "fake-key")

    expected = DEFAULT_MODELS.get(ProviderName("openai"), "")
    assert app.settings.model.model == expected


# ---------------------------------------------------------------------------
# M1-3: handle_model records model_history[current_provider]
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_model_records_history():
    """/model <name> updates model_history for the active provider."""
    from nerdvana_cli.commands.model_commands import handle_model

    app = _make_app_mock(model_history={}, current_provider="anthropic")
    app._agent_loop.registry.all_tools.return_value = []
    app.query_one.return_value = MagicMock()

    saved_config: dict = {}

    def fake_save(cfg):
        saved_config.update(cfg)

    with (
        patch("nerdvana_cli.core.setup.load_config", return_value={}),
        patch("nerdvana_cli.core.setup.save_config", side_effect=fake_save),
    ):
        await handle_model(app, "claude-opus-4")

    assert app.settings.model_history.get("anthropic") == "claude-opus-4"
    assert saved_config.get("model_history", {}).get("anthropic") == "claude-opus-4"


@pytest.mark.asyncio
async def test_switch_provider_saves_history_in_config():
    """switch_provider writes model_history to the persisted config."""
    from nerdvana_cli.commands.model_commands import switch_provider

    app = _make_app_mock(model_history={}, current_provider="anthropic")
    saved_config: dict = {}

    def fake_save(cfg):
        saved_config.update(cfg)

    with (
        patch("nerdvana_cli.core.setup.load_config", return_value={}),
        patch("nerdvana_cli.core.setup.save_config", side_effect=fake_save),
        patch("nerdvana_cli.providers.factory.create_provider") as mock_cp,
    ):
        mock_provider = AsyncMock()
        mock_provider.list_models = AsyncMock(return_value=[])
        mock_cp.return_value = mock_provider

        await switch_provider(app, "openai", "sk-test")

    assert "model_history" in saved_config
    assert "openai" in saved_config["model_history"]
