"""Finished background tasks are reported to the model exactly once.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.task_state import TaskRegistry, TaskState, TaskStatus
from nerdvana_cli.core.tool import ToolContext, ToolRegistry
from nerdvana_cli.providers.base import ProviderEvent
from nerdvana_cli.tools.agent_tool import AgentTool, AgentToolArgs
from nerdvana_cli.tools.team_tools import TaskGetArgs, TaskGetTool
from nerdvana_cli.types import Role


def _finished(registry: TaskRegistry, task_id: str, background: bool = True) -> TaskState:
    task = TaskState(id=task_id, description="look into it", status=TaskStatus.COMPLETED, output="found it")
    task.background = background
    registry.register(task)
    registry.mark_finished(task)
    return task


def test_drain_returns_each_finished_background_task_once() -> None:
    registry = TaskRegistry()
    _finished(registry, "bg-1")
    _finished(registry, "fg-1", background=False)
    running = TaskState(id="bg-2", description="still going", status=TaskStatus.RUNNING)
    running.background = True
    registry.register(running)

    assert [t.id for t in registry.drain_unreported()] == ["bg-1"]
    assert registry.drain_unreported() == []
    assert not registry.has_unreported()


def test_listeners_hear_only_background_completions() -> None:
    registry = TaskRegistry()
    heard: list[str] = []
    registry.add_listener(lambda task: heard.append(task.id))
    _finished(registry, "bg-1")
    _finished(registry, "fg-1", background=False)
    assert heard == ["bg-1"]


async def test_task_get_marks_a_finished_task_as_seen() -> None:
    registry = TaskRegistry()
    _finished(registry, "bg-1")
    await TaskGetTool(task_registry=registry).call(TaskGetArgs(task_id="bg-1"), ToolContext(), None)
    assert registry.drain_unreported() == []


async def test_background_agent_finishing_notifies_the_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    registry = TaskRegistry()
    heard: list[str] = []
    registry.add_listener(lambda task: heard.append(task.status))
    tool = AgentTool(settings=NerdvanaSettings(), task_registry=registry)

    with patch("nerdvana_cli.tools.agent_tool.run_subagent", new_callable=AsyncMock, return_value=("done", 1)):
        await tool.call(AgentToolArgs(prompt="go", run_in_background=True), ToolContext(task_registry=registry), None)
        for _ in range(20):
            if heard:
                break
            await asyncio.sleep(0.01)

    assert heard == [TaskStatus.COMPLETED]
    assert registry.has_unreported()


class _Done:
    async def stream(self, system_prompt: str, messages: Any, tools: Any) -> AsyncIterator[ProviderEvent]:
        yield ProviderEvent(type="done", stop_reason="end_turn")


async def test_loop_reports_finished_tasks_before_the_next_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: _Done())
    monkeypatch.setattr(AgentLoop, "build_system_prompt", lambda self: "system")
    registry     = TaskRegistry()
    settings     = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    loop = AgentLoop(
        settings      = settings,
        registry      = ToolRegistry(),
        session       = SessionStorage(session_id="bg", storage_dir=str(tmp_path / "sessions")),
        task_registry = registry,
    )
    task = _finished(registry, "bg-9")
    task.output = "x" * 10_000

    async for _ in loop.run("anything new?"):
        pass
    async for _ in loop.run("and now?"):
        pass

    reports = [m for m in loop.state.messages if m.role == Role.USER and "[Background task bg-9" in str(m.content)]
    assert len(reports) == 1
    assert "TaskGet bg-9" in str(reports[0].content)
