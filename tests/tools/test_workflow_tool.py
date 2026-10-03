"""The Workflow tool exists only when ``workflow.enabled`` is set, and hands the model the final output and the run id.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.delegation.task_state import TaskRegistry
from nerdvana_cli.core.delegation.workflow_store import RunStore
from nerdvana_cli.core.state.budget import Budget
from nerdvana_cli.core.subagent_config import SubagentConfig
from nerdvana_cli.core.tool import ToolCategory, ToolContext
from nerdvana_cli.tools.registry import create_tool_registry
from nerdvana_cli.tools.subagent_registry import create_subagent_registry
from nerdvana_cli.tools.workflow_tool import WorkflowTool, WorkflowToolArgs

WORKFLOW = """
name: pair
inputs:
  topic: cats
steps:
  - id: first
    agent: Explore
    prompt: look at ${inputs.topic}
  - id: second
    agent: Explore
    prompt: sum up ${steps.first.output}
"""


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "project" / ".nerdvana" / "workflows"
    directory.mkdir(parents=True)
    (directory / "pair.yml").write_text(WORKFLOW, encoding="utf-8")
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    return tmp_path / "project"


def _settings(enabled: bool) -> NerdvanaSettings:
    settings = NerdvanaSettings()
    settings.workflow.enabled = enabled
    return settings


def test_the_tool_is_registered_only_when_the_setting_is_on() -> None:
    assert create_tool_registry(settings=_settings(False)).get("Workflow") is None
    assert create_tool_registry(settings=NerdvanaSettings()).get("Workflow") is None
    tool = create_tool_registry(settings=_settings(True)).get("Workflow")
    assert isinstance(tool, WorkflowTool)
    assert tool.category == ToolCategory.META and tool.requires_confirmation


def test_a_sub_agent_is_never_handed_the_tool_and_a_session_without_settings_has_none() -> None:
    parent = create_tool_registry(settings=_settings(True))
    child  = create_subagent_registry(parent_tools=parent.all_tools())
    assert child.get("Workflow") is None
    assert create_tool_registry().get("Workflow") is None


def _tool_and_context(project: Path, budget: Budget | None = None) -> tuple[WorkflowTool, ToolContext]:
    settings     = _settings(True)
    settings.cwd = str(project)
    context      = ToolContext(cwd=str(project), task_registry=TaskRegistry())
    context.state["session_id"]     = "parent-session"
    context.state["absorb"]         = lambda usage, signals: None
    context.state["loop_factories"] = None
    if budget is not None:
        context.state["budget"] = (budget, lambda: 0.0)
    return WorkflowTool(settings=settings), context


async def test_the_tool_runs_a_named_workflow_and_returns_its_final_output_and_the_run_id(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[SubagentConfig] = []

    async def fake(config: SubagentConfig, abort: object) -> tuple[str, int]:
        seen.append(config)
        return f"[{config.prompt}]", 3

    monkeypatch.setattr("nerdvana_cli.core.delegation.workflow_engine.run_subagent", fake)
    tool, context = _tool_and_context(project)
    result = await tool.call(WorkflowToolArgs(name="pair", inputs={"topic": "dogs"}), context, None)
    assert not result.is_error
    assert result.content.startswith("[sum up [look at dogs]]")
    run_id = result.content.split("run ")[1].split(",")[0]
    assert RunStore(run_id).read_meta()["status"] == "completed"
    assert seen[0].parent_session_id == "parent-session" and seen[0].absorb is context.state["absorb"]


async def test_arguments_are_parsed_from_the_model_call_and_inputs_are_optional(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(config: SubagentConfig, abort: object) -> tuple[str, int]:
        return config.prompt, 1

    monkeypatch.setattr("nerdvana_cli.core.delegation.workflow_engine.run_subagent", fake)
    tool, context = _tool_and_context(project)
    args = tool.parse_args({"name": "pair"})
    assert (await tool.call(args, context, None)).content.startswith("sum up look at cats")


@pytest.mark.parametrize(("args", "message"), [
    (WorkflowToolArgs(name="nope"), "Unknown workflow 'nope'. Available: pair, review-fanout"),
    (WorkflowToolArgs(name="pair", inputs={"bogus": "1"}), "unknown input"),
])
async def test_an_unknown_workflow_or_input_is_an_error_result_not_a_run(project: Path, args: WorkflowToolArgs, message: str) -> None:
    tool, context = _tool_and_context(project)
    result = await tool.call(args, context, None)
    assert result.is_error and message in result.content


async def test_a_run_that_stops_returns_an_error_with_the_run_id_and_the_resume_command(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(config: SubagentConfig, abort: object) -> tuple[str, int]:
        raise RuntimeError("provider down")

    monkeypatch.setattr("nerdvana_cli.core.delegation.workflow_engine.run_subagent", fake)
    tool, context = _tool_and_context(project)
    result = await tool.call(WorkflowToolArgs(name="pair"), context, None)
    assert result.is_error and "provider down" in result.content
    assert "nerdvana workflow run pair --resume " in result.content


async def test_the_run_takes_its_ceiling_from_the_sessions_cost_limit_and_settles_what_it_spent(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shares: list[float] = []

    async def fake(config: SubagentConfig, abort: object) -> tuple[str, int]:
        shares.append(config.settings.session.max_cost_usd)
        config.cost_usd = 0.5
        return "x", 1

    monkeypatch.setattr("nerdvana_cli.core.delegation.workflow_engine.run_subagent", fake)
    budget = Budget(limit=10.0)
    tool, context = _tool_and_context(project, budget=budget)
    result: Any = await tool.call(WorkflowToolArgs(name="pair"), context, None)
    assert not result.is_error
    # subagent_budget_fraction 0.5 of 10.0 is the run's ceiling; the first agent gets a quarter of it
    assert shares[0] == pytest.approx(5.0 / 4)
    assert budget.spent == pytest.approx(1.0) and budget.promised == pytest.approx(0.0)
