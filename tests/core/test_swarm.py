"""Tests for swarm coordinator (parallel multi-agent execution)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.delegation.swarm import SwarmConfig, SwarmTask, run_swarm, swarm_warnings
from nerdvana_cli.core.delegation.task_state import TaskRegistry, TaskStatus
from nerdvana_cli.tools.subagent_registry import create_subagent_registry


@pytest.mark.asyncio
async def test_swarm_runs_all_tasks_in_parallel() -> None:
    settings      = NerdvanaSettings()
    task_registry = TaskRegistry()

    execution_order: list[str] = []

    async def _fake_subagent(config, abort):
        execution_order.append(config.agent_id)
        return f"result from {config.agent_id}", 0

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

    with patch("nerdvana_cli.core.delegation.swarm.run_subagent", side_effect=_fake_subagent):
        results = await run_swarm(config, create_subagent_registry)

    assert len(results) == 3
    assert all(r.startswith("result from") for r in results.values())
    for task in tasks:
        assert any(task.name in k for k in results)


@pytest.mark.asyncio
async def test_swarm_marks_tasks_completed() -> None:
    settings      = NerdvanaSettings()
    task_registry = TaskRegistry()

    async def _fake_subagent(config, abort):
        return "done", 0

    tasks  = [SwarmTask(name="w1", prompt="p1"), SwarmTask(name="w2", prompt="p2")]
    config = SwarmConfig(
        team_name     = "test-swarm",
        tasks         = tasks,
        settings      = settings,
        task_registry = task_registry,
    )

    with patch("nerdvana_cli.core.delegation.swarm.run_subagent", side_effect=_fake_subagent):
        await run_swarm(config, create_subagent_registry)

    completed = [t for t in task_registry.all() if t.status == TaskStatus.COMPLETED]
    assert len(completed) == 2


@pytest.mark.asyncio
async def test_swarm_handles_partial_failure() -> None:
    settings      = NerdvanaSettings()
    task_registry = TaskRegistry()

    async def _failing(config, abort):
        if "worker-2" in config.agent_id:
            raise RuntimeError("worker-2 failed")
        return "ok", 0

    tasks  = [SwarmTask(name="worker-1", prompt="p1"), SwarmTask(name="worker-2", prompt="p2")]
    config = SwarmConfig(
        team_name     = "swarm",
        tasks         = tasks,
        settings      = settings,
        task_registry = task_registry,
    )

    with patch("nerdvana_cli.core.delegation.swarm.run_subagent", side_effect=_failing):
        results = await run_swarm(config, create_subagent_registry)

    assert any("ok" in v for v in results.values())
    assert any("[swarm error]" in v for v in results.values())


@pytest.mark.asyncio
async def test_a_task_that_waits_for_another_runs_after_it() -> None:
    settings      = NerdvanaSettings()
    task_registry = TaskRegistry()

    execution_order: list[str] = []

    async def _fake_subagent(config, abort):
        execution_order.append(config.agent_id)
        return "done", 0

    tasks = [
        SwarmTask(name="first",  prompt="p1"),
        SwarmTask(name="second", prompt="p2", blocked_by=("first",)),
    ]
    config = SwarmConfig(
        team_name     = "test-swarm",
        tasks         = tasks,
        settings      = settings,
        task_registry = task_registry,
    )

    with patch("nerdvana_cli.core.delegation.swarm.run_subagent", side_effect=_fake_subagent):
        results = await run_swarm(config, create_subagent_registry)

    assert len(results) == 2
    assert execution_order.index("first@test-swarm") < execution_order.index("second@test-swarm")


def test_the_tasks_that_write_the_same_path_are_warned_about() -> None:
    tasks = [
        SwarmTask(name="a", prompt="p1", write_scopes=("nerdvana_cli/core/state",)),
        SwarmTask(name="b", prompt="p2", write_scopes=("nerdvana_cli/core/state/session.py",)),
        SwarmTask(name="c", prompt="p3", write_scopes=("nerdvana_cli/cli",)),
    ]

    warnings = swarm_warnings(tasks)

    assert any("a:" in warning and "b" in warning for warning in warnings)
    assert any("b:" in warning and "a" in warning for warning in warnings)
    assert not any("c:" in warning for warning in warnings)
