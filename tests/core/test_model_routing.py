"""Sub-agent model routing: precedence, provider switches and the configuration section.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.agents.registry import AgentTypeRegistry
from nerdvana_cli.core.model_routing import apply_model_spec, point_settings_at, select_model
from nerdvana_cli.core.settings import NerdvanaSettings

CATEGORIES = {"quick": "claude-haiku-4-5-20251001", "deep": "openai:gpt-4.1"}


def test_the_call_argument_beats_the_definition_and_the_definition_beats_the_category() -> None:
    assert select_model("m-arg", "quick", "m-def", "deep", CATEGORIES) == "m-arg"
    assert select_model("", "quick", "m-def", "deep", CATEGORIES) == "m-def"
    assert select_model("", "quick", "", "deep", CATEGORIES) == CATEGORIES["quick"]
    assert select_model("", "", "", "deep", CATEGORIES) == CATEGORIES["deep"]


def test_nothing_named_means_inherit() -> None:
    assert select_model("", "", "", "", CATEGORIES) == ""
    assert select_model("", "unmapped", "", "", CATEGORIES) == ""


def test_a_bare_model_stays_on_the_parents_provider() -> None:
    settings = NerdvanaSettings()
    provider = settings.model.provider
    assert apply_model_spec(settings, "claude-haiku-4-5-20251001")
    assert settings.model.model == "claude-haiku-4-5-20251001"
    assert settings.model.provider == provider


def test_another_provider_takes_its_credential_and_drops_the_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = NerdvanaSettings()
    settings.model.base_url = "https://parent.example"
    assert apply_model_spec(settings, "openai:gpt-4.1")
    assert (settings.model.provider, settings.model.model, settings.model.api_key, settings.model.base_url) == (
        "openai", "gpt-4.1", "sk-test", "",
    )


def test_a_provider_without_a_credential_leaves_the_parents_model_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = NerdvanaSettings()
    before   = (settings.model.provider, settings.model.model, settings.model.api_key)
    assert not apply_model_spec(settings, "openai:gpt-4.1")
    assert (settings.model.provider, settings.model.model, settings.model.api_key) == before


def test_an_empty_spec_changes_nothing() -> None:
    settings = NerdvanaSettings()
    before   = settings.model.model
    assert not apply_model_spec(settings, "")
    assert settings.model.model == before


def test_point_settings_at_reports_the_missing_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert point_settings_at(NerdvanaSettings(), "gemini", "gemini-2.5-flash") is False


def test_a_definition_file_can_name_a_model_and_a_category(tmp_path: Path) -> None:
    (tmp_path / "scout.yml").write_text("name: scout\nmodel: claude-haiku-4-5-20251001\ncategory: quick\n", encoding="utf-8")
    registry = AgentTypeRegistry()
    registry.load_from_dir(str(tmp_path))
    definition = registry.get("scout")
    assert definition is not None
    assert (definition.model, definition.category) == ("claude-haiku-4-5-20251001", "quick")


def test_the_categories_section_loads_from_the_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    config = tmp_path / "nerdvana.yml"
    config.write_text("agents:\n  categories:\n    quick: claude-haiku-4-5-20251001\n", encoding="utf-8")
    settings = NerdvanaSettings.load(str(config))
    assert settings.agents.categories == {"quick": "claude-haiku-4-5-20251001"}
    assert not [w for w in settings.load_warnings if w.kind == "unknown_key"]


async def _child_model(settings: NerdvanaSettings, args: object) -> str:
    from unittest.mock import AsyncMock, patch

    from nerdvana_cli.core.task_state import TaskRegistry
    from nerdvana_cli.core.tool import ToolContext
    from nerdvana_cli.tools.agent_tool import AgentTool

    registry = TaskRegistry()
    tool     = AgentTool(settings=settings, task_registry=registry)
    with patch("nerdvana_cli.tools.agent_tool.run_subagent", new_callable=AsyncMock, return_value=("ok", 1)) as run:
        await tool.call(args, ToolContext(cwd=".", task_registry=registry), can_use_tool=None)  # type: ignore[arg-type]
    return str(run.await_args.args[0].settings.model.model)


async def test_the_agent_tool_runs_the_child_on_the_category_model() -> None:
    from nerdvana_cli.tools.agent_tool import AgentToolArgs

    settings = NerdvanaSettings()
    settings.agents.categories = {"quick": "claude-haiku-4-5-20251001"}
    assert await _child_model(settings, AgentToolArgs(prompt="p", category="quick")) == "claude-haiku-4-5-20251001"
    assert await _child_model(settings, AgentToolArgs(prompt="p", category="quick", model="explicit-model")) == "explicit-model"
    assert await _child_model(settings, AgentToolArgs(prompt="p")) == settings.model.model
    assert settings.model.model != "claude-haiku-4-5-20251001"
