"""A background Agent task leaves a durable run record: started, renewed while it runs, closed with its outcome.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core import run_store
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.run_store import FAILED, RUNNING, STOPPED, SUCCEEDED, RunStore
from nerdvana_cli.core.subagent_config import SubagentConfig
from nerdvana_cli.core.task_state import TaskRegistry
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.agent_tool import AgentTool, AgentToolArgs
from nerdvana_cli.tools.team_tools import TaskStopArgs, TaskStopTool


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TaskRegistry:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    return TaskRegistry()


async def _start(registry: TaskRegistry, fake: Any, monkeypatch: pytest.MonkeyPatch, cwd: str = ".", **args: object) -> str:
    """Start a background Agent whose run is *fake*; the replacement stays in place until the test ends, as the task runs later."""
    monkeypatch.setattr("nerdvana_cli.tools.agent_tool.run_subagent", fake)
    result = await AgentTool(settings=NerdvanaSettings(), task_registry=registry).call(
        AgentToolArgs(prompt="survey the repo", description="survey", run_in_background=True, **args),  # type: ignore[arg-type]
        ToolContext(cwd=cwd, task_registry=registry), can_use_tool=None,
    )
    return result.content.rsplit("Task ID: ", 1)[1]


async def test_the_task_is_recorded_while_it_runs_and_closed_with_its_output(registry: TaskRegistry, monkeypatch: pytest.MonkeyPatch) -> None:
    release = asyncio.Event()

    async def fake(config: SubagentConfig, abort: asyncio.Event) -> tuple[str, int]:
        await release.wait()
        config.cost_usd = 0.125
        return "found three modules", 40

    task_id = await _start(registry, fake, monkeypatch, cwd="/work")
    store   = RunStore()
    running = store.load(task_id)
    assert running is not None and running.status == RUNNING and running.kind == "task"
    assert running.prompt == "survey the repo" and running.cwd == "/work" and running.owner_pid == os.getpid()
    assert running.session_id == task_id and store.effective_status(running) == RUNNING

    release.set()
    task = registry.get(task_id)
    assert task is not None and task.bg_task is not None
    await task.bg_task
    done = store.load(task_id)
    assert done is not None and done.status == SUCCEEDED and done.cost_usd == 0.125 and done.finished_at is not None
    assert Path(done.result_path).read_text() == "found three modules"


async def test_a_failing_task_is_recorded_failed_with_the_error(registry: TaskRegistry, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(config: SubagentConfig, abort: asyncio.Event) -> tuple[str, int]:
        raise RuntimeError("provider down")

    task_id = await _start(registry, fake, monkeypatch)
    task = registry.get(task_id)
    assert task is not None and task.bg_task is not None
    await task.bg_task
    done = RunStore().load(task_id)
    assert done is not None and done.status == FAILED and "provider down" in done.error


async def test_stopping_the_task_closes_its_record_as_stopped_and_cancels_the_run(registry: TaskRegistry, monkeypatch: pytest.MonkeyPatch) -> None:
    cancelled = asyncio.Event()
    started   = asyncio.Event()

    async def fake(config: SubagentConfig, abort: asyncio.Event) -> tuple[str, int]:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "late", 0

    task_id = await _start(registry, fake, monkeypatch)
    await started.wait()
    await TaskStopTool(registry).call(TaskStopArgs(task_id=task_id), ToolContext(cwd=".", task_registry=registry), can_use_tool=None)
    task = registry.get(task_id)
    assert task is not None and task.bg_task is not None
    await asyncio.gather(task.bg_task, return_exceptions=True)
    assert cancelled.is_set()
    done = RunStore().load(task_id)
    assert done is not None and done.status == STOPPED and done.finished_at is not None


async def test_the_lease_is_renewed_while_the_task_runs(registry: TaskRegistry, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_store, "HEARTBEAT_SECONDS", 0.05)
    release = asyncio.Event()

    async def fake(config: SubagentConfig, abort: asyncio.Event) -> tuple[str, int]:
        await release.wait()
        return "ok", 1

    task_id = await _start(registry, fake, monkeypatch)
    store   = RunStore()
    store.update(task_id, heartbeat_at=1.0)
    await asyncio.sleep(0.3)
    renewed = store.load(task_id)
    assert renewed is not None and renewed.heartbeat_at > 1000.0
    release.set()
    task = registry.get(task_id)
    assert task is not None and task.bg_task is not None
    await task.bg_task


async def test_a_task_in_a_worktree_records_where_its_changes_are(registry: TaskRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)

    async def fake(config: SubagentConfig, abort: asyncio.Event) -> tuple[str, int]:
        (Path(config.settings.cwd) / "made.txt").write_text("x")
        return "edited", 1

    task_id = await _start(registry, fake, monkeypatch, cwd=str(repo), isolation="worktree")
    task = registry.get(task_id)
    assert task is not None and task.bg_task is not None
    await task.bg_task
    done = RunStore().load(task_id)
    assert done is not None and Path(done.worktree_path, "made.txt").is_file() and done.worktree_branch.startswith("nerdvana/")
    subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", done.worktree_path], check=True, capture_output=True)


async def test_a_store_that_cannot_be_written_does_not_stop_the_task(registry: TaskRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the data root should be")
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(blocked))

    async def fake(config: SubagentConfig, abort: asyncio.Event) -> tuple[str, int]:
        return "still works", 1

    task_id = await _start(registry, fake, monkeypatch)
    task = registry.get(task_id)
    assert task is not None and task.bg_task is not None
    output, _ = await task.bg_task
    assert output == "still works"
