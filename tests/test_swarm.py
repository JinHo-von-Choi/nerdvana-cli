"""Tests for swarm coordinator (parallel multi-agent execution)."""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import patch

from nerdvana_cli.core.settings   import NerdvanaSettings
from nerdvana_cli.core.task_state import TaskRegistry, TaskStatus
from nerdvana_cli.core.swarm      import SwarmConfig, SwarmTask, run_swarm


@pytest.mark.asyncio
async def test_swarm_runs_all_tasks_in_parallel() -> None:
    settings      = NerdvanaSettings()
    task_registry = TaskRegistry()

    execution_order: list[str] = []

    async def _fake_subagent(config, abort):
        execution_order.append(config.agent_id)
        return f"result from {config.agent_id}"

    tasks  = [
        SwarmTask(name="worker-1", prompt="task 1"),
        SwarmTask(name="worker-2", prompt="task 2"),
        SwarmTask(name="worker-3", prompt="task 3"),
    ]
    config = SwarmConfig(
        team_name     = "test-swarm",
        tasks         = tasks,
        settings      = settings,
        task_registry = task_registry,
    )

    with patch("nerdvana_cli.core.swarm.run_subagent", side_effect=_fake_subagent):
        results = await run_swarm(config)

    assert len(results) == 3
    assert all(r.startswith("result from") for r in results.values())
    for task in tasks:
        assert any(task.name in k for k in results)


@pytest.mark.asyncio
async def test_swarm_marks_tasks_completed() -> None:
    settings      = NerdvanaSettings()
    task_registry = TaskRegistry()

    async def _fake_subagent(config, abort):
        return "done"

    tasks  = [SwarmTask(name="w1", prompt="p1"), SwarmTask(name="w2", prompt="p2")]
    config = SwarmConfig(
        team_name     = "test-swarm",
        tasks         = tasks,
        settings      = settings,
        task_registry = task_registry,
    )

    with patch("nerdvana_cli.core.swarm.run_subagent", side_effect=_fake_subagent):
        await run_swarm(config)

    completed = [t for t in task_registry.all() if t.status == TaskStatus.COMPLETED]
    assert len(completed) == 2


@pytest.mark.asyncio
async def test_swarm_handles_partial_failure() -> None:
    settings      = NerdvanaSettings()
    task_registry = TaskRegistry()

    async def _failing(config, abort):
        if "worker-2" in config.agent_id:
            raise RuntimeError("worker-2 failed")
        return "ok"

    tasks  = [SwarmTask(name="worker-1", prompt="p1"), SwarmTask(name="worker-2", prompt="p2")]
    config = SwarmConfig(
        team_name     = "swarm",
        tasks         = tasks,
        settings      = settings,
        task_registry = task_registry,
    )

    with patch("nerdvana_cli.core.swarm.run_subagent", side_effect=_failing):
        results = await run_swarm(config)

    assert any("ok" in v for v in results.values())
    assert any("[swarm error]" in v for v in results.values())
