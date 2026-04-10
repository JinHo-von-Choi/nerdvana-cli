from nerdvana_cli.agents.registry import AgentTypeRegistry
from nerdvana_cli.agents.builtin  import BUILTIN_AGENTS


def test_builtin_agents_are_registered() -> None:
    reg = AgentTypeRegistry()
    for agent in BUILTIN_AGENTS:
        reg.register(agent)
    assert reg.get("general-purpose") is not None
    assert reg.get("Explore") is not None


def test_agent_type_registry_unknown_returns_none() -> None:
    reg = AgentTypeRegistry()
    assert reg.get("nonexistent") is None


import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from nerdvana_cli.core.settings    import NerdvanaSettings
from nerdvana_cli.core.task_state  import TaskRegistry, TaskStatus
from nerdvana_cli.core.tool        import ToolContext
from nerdvana_cli.tools.agent_tool import AgentTool, AgentToolArgs


@pytest.mark.asyncio
async def test_agent_tool_foreground_returns_output() -> None:
    settings      = NerdvanaSettings()
    task_registry = TaskRegistry()
    tool          = AgentTool(settings=settings, task_registry=task_registry)
    ctx           = ToolContext(cwd=".", task_registry=task_registry)

    with patch(
        "nerdvana_cli.tools.agent_tool.run_subagent",
        new_callable=AsyncMock,
        return_value="agent output",
    ):
        result = await tool.call(
            AgentToolArgs(prompt="do a task"),
            ctx,
            can_use_tool=None,
        )

    assert result.content == "agent output"
    assert not result.is_error


@pytest.mark.asyncio
async def test_agent_tool_background_returns_task_id() -> None:
    settings      = NerdvanaSettings()
    task_registry = TaskRegistry()
    tool          = AgentTool(settings=settings, task_registry=task_registry)
    ctx           = ToolContext(cwd=".", task_registry=task_registry)

    async def _noop(*_a, **_kw):
        return "done"

    with patch("nerdvana_cli.tools.agent_tool.run_subagent", side_effect=_noop):
        result = await tool.call(
            AgentToolArgs(prompt="background task", run_in_background=True),
            ctx,
            can_use_tool=None,
        )

    assert "Task ID:" in result.content
    task_id = result.content.split("Task ID:")[-1].strip()
    assert task_registry.get(task_id) is not None


@pytest.mark.asyncio
async def test_agent_tool_marks_task_completed() -> None:
    settings      = NerdvanaSettings()
    task_registry = TaskRegistry()
    tool          = AgentTool(settings=settings, task_registry=task_registry)
    ctx           = ToolContext(cwd=".", task_registry=task_registry)

    with patch(
        "nerdvana_cli.tools.agent_tool.run_subagent",
        new_callable=AsyncMock,
        return_value="result",
    ):
        await tool.call(
            AgentToolArgs(prompt="task"),
            ctx,
            can_use_tool=None,
        )

    tasks = task_registry.all()
    assert len(tasks) == 1
    assert tasks[0].status == TaskStatus.COMPLETED
