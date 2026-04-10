"""Swarm coordinator — runs multiple subagents in parallel and aggregates results."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass

from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.subagent import SubagentConfig, run_subagent
from nerdvana_cli.core.task_state import TaskRegistry, TaskState, TaskStatus
from nerdvana_cli.tools.registry import create_subagent_registry


@dataclass
class SwarmTask:
    """Single work item dispatched to one swarm worker."""

    name:          str
    prompt:        str
    subagent_type: str = "general-purpose"
    model:         str = ""


@dataclass
class SwarmConfig:
    """Configuration for a full swarm run."""

    team_name:     str
    tasks:         list[SwarmTask]
    settings:      NerdvanaSettings
    task_registry: TaskRegistry
    max_turns:     int = 50


async def run_swarm(config: SwarmConfig) -> dict[str, str]:
    """Dispatch all swarm tasks in parallel, return {agent_id: output} map.

    Partial failures are captured and returned as "[swarm error] ..." strings
    so the leader can inspect them without crashing.
    """
    async def _run_one(task: SwarmTask) -> tuple[str, str]:
        agent_id   = f"{task.name}@{config.team_name}"
        abort      = asyncio.Event()
        task_state = TaskState(
            id          = agent_id,
            description = task.prompt[:80],
            status      = TaskStatus.RUNNING,
            abort       = abort,
        )
        config.task_registry.register(task_state)

        child_settings            = copy.deepcopy(config.settings)
        child_settings.session.max_turns = config.max_turns
        if task.model:
            child_settings.model.model = task.model

        child_registry = create_subagent_registry(child_settings)
        sub_config     = SubagentConfig(
            agent_id  = agent_id,
            name      = task.subagent_type,
            prompt    = task.prompt,
            settings  = child_settings,
            registry  = child_registry,
            max_turns = config.max_turns,
        )

        try:
            output             = await run_subagent(sub_config, abort)
            task_state.status  = TaskStatus.COMPLETED
            task_state.output  = output
            return agent_id, output
        except Exception as exc:
            task_state.status = TaskStatus.FAILED
            task_state.error  = str(exc)
            return agent_id, f"[swarm error] {exc}"

    results_list = await asyncio.gather(*[_run_one(t) for t in config.tasks])
    return dict(results_list)
