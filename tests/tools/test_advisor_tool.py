"""The Advisor tool: when it is registered, what it returns, and who never gets it.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core import paths as core_paths
from nerdvana_cli.core.advisor import Advice
from nerdvana_cli.core.managed_policy import load_managed_policy
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import ToolCategory, ToolContext
from nerdvana_cli.tools.advisor_tool import AdvisorArgs, AdvisorTool
from nerdvana_cli.tools.registry import create_tool_registry
from nerdvana_cli.tools.subagent_registry import create_subagent_registry


class _Advisor:
    def __init__(self, advice: Advice) -> None:
        self.advice  = advice
        self.asked: list[str] = []

    async def advise(self, question: str, reason: str = "") -> Advice:
        self.asked.append(question)
        return self.advice


def _settings(tmp_path: Path, enabled: bool) -> NerdvanaSettings:
    settings = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    settings.advisor.enabled = enabled
    return settings


def test_the_tool_is_registered_only_when_the_advisor_is_enabled(tmp_path: Path) -> None:
    off = create_tool_registry(settings=_settings(tmp_path, False))
    on  = create_tool_registry(settings=_settings(tmp_path, True))
    assert off.get("Advisor") is None
    assert isinstance(on.get("Advisor"), AdvisorTool)


def test_a_registry_built_without_settings_has_no_advisor() -> None:
    assert create_tool_registry().get("Advisor") is None


def test_the_advisor_is_off_by_default() -> None:
    config = NerdvanaSettings().advisor
    assert (config.enabled, config.model, config.max_calls, config.max_context_messages, config.on_signals) == (False, "", 3, 12, False)


def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("NERDVANA_MANAGED_DIR", raising=False)
    monkeypatch.delenv("NERDVANA_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(core_paths, "system_managed_dir", lambda: tmp_path / "system")
    monkeypatch.chdir(tmp_path)


def test_the_advisor_and_phase_effort_settings_load_from_the_config_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _isolated(monkeypatch, tmp_path)
    path = tmp_path / "config.yml"
    path.write_text(
        "advisor:\n  enabled: true\n  model: openai:gpt-5\n  max_calls: 5\n  max_context_messages: 6\n  on_signals: true\n"
        "model:\n  reasoning_effort: high\n  effort_planning: max\n  effort_implementation: medium\n  effort_verification: xhigh\n",
        encoding="utf-8",
    )
    settings = NerdvanaSettings.load(str(path))
    assert [w.format() for w in settings.load_warnings] == []
    advisor = settings.advisor
    assert (advisor.enabled, advisor.model, advisor.max_calls, advisor.max_context_messages, advisor.on_signals) == (True, "openai:gpt-5", 5, 6, True)
    assert (settings.model.effort_planning, settings.model.effort_implementation, settings.model.effort_verification) == ("max", "medium", "xhigh")


def test_a_bad_advisor_value_falls_back_to_its_default_with_a_warning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _isolated(monkeypatch, tmp_path)
    path = tmp_path / "config.yml"
    path.write_text("advisor:\n  max_calls: -1\n  max_context_messages: 0\n  enabled: true\n", encoding="utf-8")
    settings = NerdvanaSettings.load(str(path))
    assert (settings.advisor.max_calls, settings.advisor.max_context_messages, settings.advisor.enabled) == (3, 12, True)
    assert sorted(w.path for w in settings.load_warnings) == ["advisor.max_calls", "advisor.max_context_messages"]


def test_sub_agents_never_get_the_tool(tmp_path: Path) -> None:
    parent = create_tool_registry(settings=_settings(tmp_path, True))
    child  = create_subagent_registry(allowed_tools=["*"], parent_tools=parent.all_tools())
    assert child.get("Advisor") is None
    assert AdvisorTool.category == ToolCategory.META


def test_the_declaration_asks_for_one_question_and_says_when_to_use_it() -> None:
    tool = AdvisorTool()
    assert tool.input_schema["required"] == ["question"]
    assert "decision point" in tool.description_text and "limited" in tool.description_text


async def test_the_result_is_the_advice(tmp_path: Path) -> None:
    advisor = _Advisor(Advice("Patch it.\n\n[Advisor consultations left in this run: 2]"))
    context = ToolContext(cwd=str(tmp_path))
    context.state["advisor"] = advisor
    result = await AdvisorTool().call(AdvisorArgs("rewrite or patch?"), context)
    assert result.content.startswith("Patch it.") and not result.is_error
    assert advisor.asked == ["rewrite or patch?"]


async def test_a_refusal_is_an_error_result(tmp_path: Path) -> None:
    context = ToolContext(cwd=str(tmp_path))
    context.state["advisor"] = _Advisor(Advice("The advisor was not called: limit.", ok=False))
    result = await AdvisorTool().call(AdvisorArgs("q"), context)
    assert result.is_error and "not called" in result.content


async def test_without_an_advisor_in_the_context_the_call_says_so(tmp_path: Path) -> None:
    result = await AdvisorTool().call(AdvisorArgs("q"), ToolContext(cwd=str(tmp_path)))
    assert result.is_error and "not available" in result.content


def test_an_empty_question_is_refused_before_any_request() -> None:
    tool = AdvisorTool()
    assert tool.validate_input(AdvisorArgs("   "), ToolContext()) == "question must not be empty"
    assert tool.validate_input(AdvisorArgs("which?"), ToolContext()) is None


# ---------------------------------------------------------------------------
# The managed policy
# ---------------------------------------------------------------------------


def test_an_advisor_model_outside_the_managed_policy_is_dropped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    managed = tmp_path / "managed.d"
    managed.mkdir()
    (managed / "10-m.yml").write_text("model:\n  allowed_models: ['claude-*']\n", encoding="utf-8")
    monkeypatch.setenv("NERDVANA_MANAGED_DIR", str(managed))
    monkeypatch.setattr(core_paths, "system_managed_dir", lambda: tmp_path / "system")
    settings: Any = _settings(tmp_path, True)
    settings.advisor.model = "openai:gpt-4o"
    policy = load_managed_policy()
    policy.apply(settings)
    assert settings.advisor.model == ""
    settings.advisor.model = "claude-opus-5-5"
    policy.apply(settings)
    assert settings.advisor.model == "claude-opus-5-5"
