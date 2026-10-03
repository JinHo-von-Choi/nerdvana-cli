from nerdvana_cli.agents.builtin import BUILTIN_AGENTS
from nerdvana_cli.agents.registry import AgentTypeRegistry


def test_builtin_agents_are_registered() -> None:
    reg = AgentTypeRegistry()
    for agent in BUILTIN_AGENTS:
        reg.register(agent)
    assert reg.get("general-purpose") is not None
    assert reg.get("Explore") is not None


def test_agent_type_registry_unknown_returns_none() -> None:
    reg = AgentTypeRegistry()
    assert reg.get("nonexistent") is None


from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.task_state import TaskRegistry, TaskStatus
from nerdvana_cli.core.tool import ToolContext
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
        return_value=("agent output", 100),
    ):
        result = await tool.call(
            AgentToolArgs(prompt="do a task"),
            ctx,
            can_use_tool=None,
        )

    assert result.content == "agent output"
    assert not result.is_error


@pytest.mark.asyncio
async def test_agent_tool_background_returns_task_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    settings      = NerdvanaSettings()
    task_registry = TaskRegistry()
    tool          = AgentTool(settings=settings, task_registry=task_registry)
    ctx           = ToolContext(cwd=".", task_registry=task_registry)

    async def _noop(*_a, **_kw):
        return "done", 0

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
        return_value=("result", 100),
    ):
        await tool.call(
            AgentToolArgs(prompt="task"),
            ctx,
            can_use_tool=None,
        )

    tasks = task_registry.all()
    assert len(tasks) == 1
    assert tasks[0].status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_agent_tool_passes_definition_prompt_and_turn_limit() -> None:
    settings      = NerdvanaSettings()
    task_registry = TaskRegistry()
    tool          = AgentTool(settings=settings, task_registry=task_registry)
    ctx           = ToolContext(cwd=".", task_registry=task_registry)
    explore       = next(a for a in BUILTIN_AGENTS if a.agent_type == "Explore")

    with patch(
        "nerdvana_cli.tools.agent_tool.run_subagent",
        new_callable=AsyncMock,
        return_value=("found", 10),
    ) as runner:
        await tool.call(AgentToolArgs(prompt="look around", subagent_type="Explore"), ctx, None)

    config = runner.await_args.args[0]
    assert config.system_prompt == explore.system_prompt
    assert config.system_prompt
    assert config.max_turns == explore.max_turns
    assert config.settings.session.max_turns == explore.max_turns


@pytest.mark.asyncio
async def test_role_prompt_reaches_the_child_system_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from nerdvana_cli.core.agent_loop import AgentLoop
    from nerdvana_cli.core.session import SessionStorage
    from nerdvana_cli.core.tool import ToolRegistry

    seen: list[str] = []

    class _Provider:
        async def stream(self, system_prompt: str, messages: object, tools: object):  # type: ignore[no-untyped-def]
            from nerdvana_cli.providers.base import ProviderEvent
            seen.append(system_prompt)
            yield ProviderEvent(type="done", stop_reason="end_turn")

    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    with (
        patch.object(AgentLoop, "create_provider_from_settings", lambda self: _Provider()),
        patch.object(AgentLoop, "build_system_prompt", lambda self: "base"),
    ):
        settings     = NerdvanaSettings()
        settings.cwd = str(tmp_path)
        settings.session.persist = False
        loop = AgentLoop(
            settings    = settings,
            registry    = ToolRegistry(),
            session     = SessionStorage(session_id="role-prompt", storage_dir=str(tmp_path / "sessions")),
            role_prompt = "Read only. Never edit.",
        )
        async for _ in loop.run("go"):
            pass

    assert "Read only. Never edit." in seen[0]
