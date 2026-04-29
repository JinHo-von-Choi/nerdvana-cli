"""Tests for M2: selector highlighted cursor positioning.

handle_provider and handle_models set selector.highlighted to the index of
the current provider/model. This module tests the index-resolution logic
extracted from those handlers via targeted unit tests on helper stubs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers: index resolution utilities mirroring handler logic
# ---------------------------------------------------------------------------

def _resolve_provider_index(current_provider: str, provider_names: list[str]) -> int:
    """Return the index of current_provider in provider_names, or 0."""
    for idx, name in enumerate(provider_names):
        if name == current_provider:
            return idx
    return 0


def _resolve_model_index(current_model: str, model_ids: list[str]) -> int:
    """Return the index of current_model in model_ids, or 0."""
    for idx, mid in enumerate(model_ids):
        if mid == current_model:
            return idx
    return 0


# ---------------------------------------------------------------------------
# M2-1: provider index resolution
# ---------------------------------------------------------------------------

def test_provider_index_current_found():
    providers = ["anthropic", "openai", "gemini", "dashscope"]
    assert _resolve_provider_index("openai", providers) == 1
    assert _resolve_provider_index("gemini", providers) == 2


def test_provider_index_current_not_found():
    providers = ["anthropic", "openai", "gemini"]
    assert _resolve_provider_index("unknown", providers) == 0


def test_provider_index_first_element():
    providers = ["anthropic", "openai"]
    assert _resolve_provider_index("anthropic", providers) == 0


# ---------------------------------------------------------------------------
# M2-2: model index resolution
# ---------------------------------------------------------------------------

def test_model_index_current_found():
    models = ["gpt-4o", "gpt-4.1", "gpt-4o-mini"]
    assert _resolve_model_index("gpt-4.1", models) == 1


def test_model_index_not_in_list():
    """When current model is absent, fallback to 0."""
    models = ["gpt-4o", "gpt-4.1"]
    assert _resolve_model_index("old-model-no-longer-listed", models) == 0


def test_model_index_empty_list():
    assert _resolve_model_index("anything", []) == 0


# ---------------------------------------------------------------------------
# M2-3: handle_provider sets highlighted on the selector (integration-lite)
# ---------------------------------------------------------------------------

def _make_app_for_provider(current_provider: str) -> MagicMock:
    app = MagicMock()
    app.settings.model.provider = current_provider
    app.settings.model.model = "test-model"
    app.settings.model_history = {}
    return app


@pytest.mark.asyncio
async def test_handle_provider_sets_highlighted():
    """handle_provider must set prov_selector.highlighted = current provider index."""
    from nerdvana_cli.commands.model_commands import handle_provider

    app = _make_app_for_provider("openai")

    # Build a fake OptionList-like selector that records highlighted assignment
    recorded: dict = {}

    class FakeSel:
        def clear_options(self): pass
        def add_option(self, _opt): pass
        def add_class(self, _cls): pass
        def focus(self): pass
        @property
        def highlighted(self): return recorded.get("highlighted")
        @highlighted.setter
        def highlighted(self, v): recorded["highlighted"] = v

    app.query_one.return_value = FakeSel()

    from nerdvana_cli.providers.base import ProviderName
    provider_list = list(ProviderName)
    expected_idx = next(
        (i for i, p in enumerate(provider_list) if p.value == "openai"), 0
    )

    await handle_provider(app, "")

    assert recorded.get("highlighted") == expected_idx


@pytest.mark.asyncio
async def test_handle_models_sets_highlighted():
    """handle_models must set selector.highlighted = current model index."""
    from nerdvana_cli.commands.model_commands import handle_models

    app = MagicMock()
    app.settings.model.model = "gpt-4.1"
    app.settings.model.provider = "openai"

    model_ids = ["gpt-4o", "gpt-4.1", "gpt-4o-mini"]

    class FakeModel:
        def __init__(self, mid): self.id = mid

    mock_provider = AsyncMock()
    mock_provider.list_models = AsyncMock(return_value=[FakeModel(m) for m in model_ids])
    app._agent_loop.provider = mock_provider

    recorded: dict = {}

    class FakeSel:
        def clear_options(self): pass
        def add_option(self, _opt): pass
        def add_class(self, _cls): pass
        def focus(self): pass
        @property
        def highlighted(self): return recorded.get("highlighted")
        @highlighted.setter
        def highlighted(self, v): recorded["highlighted"] = v

    app.query_one.return_value = FakeSel()

    await handle_models(app, "")

    assert recorded.get("highlighted") == 1  # "gpt-4.1" is at index 1


@pytest.mark.asyncio
async def test_handle_models_highlighted_fallback_when_not_in_list():
    """handle_models uses index 0 when current model is absent from list."""
    from nerdvana_cli.commands.model_commands import handle_models

    app = MagicMock()
    app.settings.model.model = "deprecated-model"
    app.settings.model.provider = "openai"

    model_ids = ["gpt-4o", "gpt-4.1"]

    class FakeModel:
        def __init__(self, mid): self.id = mid

    mock_provider = AsyncMock()
    mock_provider.list_models = AsyncMock(return_value=[FakeModel(m) for m in model_ids])
    app._agent_loop.provider = mock_provider

    recorded: dict = {}

    class FakeSel:
        def clear_options(self): pass
        def add_option(self, _opt): pass
        def add_class(self, _cls): pass
        def focus(self): pass
        @property
        def highlighted(self): return recorded.get("highlighted", -1)
        @highlighted.setter
        def highlighted(self, v): recorded["highlighted"] = v

    app.query_one.return_value = FakeSel()

    await handle_models(app, "")

    assert recorded.get("highlighted") == 0
