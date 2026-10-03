"""Swarm coordinator — runs multiple subagents in parallel and aggregates results."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nerdvana_cli.core.budget import Budget, Envelope
from nerdvana_cli.core.config.model_routing import apply_model_spec, select_model
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.subagent import label_confirm, run_subagent
from nerdvana_cli.core.subagent_config import LoopFactories, SubagentConfig
from nerdvana_cli.core.task_state import TaskRegistry, TaskState, TaskStatus
from nerdvana_cli.core.tool import ConfirmCallback, ToolRegistry


@dataclass
class SwarmTask:
    """Single work item dispatched to one swarm worker."""

    name:          str
    prompt:        str
    subagent_type: str = "general-purpose"
    model:         str = ""
    category:      str = ""


@dataclass
class SwarmConfig:
    """Configuration for a full swarm run."""

    team_name:     str
    tasks:         list[SwarmTask]
    settings:      NerdvanaSettings
    task_registry: TaskRegistry
    max_turns:     int = 50
    confirm:       ConfirmCallback | None = None
    parent_session_id: str = ""
    # (Budget, callable returning the leader's own spend) from the leader's tool context, or None.
    budget:        Any = None
    # Adds a finished worker's token totals and signals to the leader's.
    absorb:        Any = None
    # The factories each worker's loop is built with.
    factories:     LoopFactories | None = None


async def run_swarm(
    config:           SwarmConfig,
    registry_factory: Callable[[NerdvanaSettings], ToolRegistry],
) -> dict[str, str]:
    """Dispatch all swarm tasks in parallel, return {agent_id: output} map.

    *registry_factory* builds each worker's tool registry from its settings.

    Partial failures are captured and returned as "[swarm error] ..." strings
    so the leader can inspect them without crashing.
    """
    spent: list[float] = []
    share  = _reserve_share(config)

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

        child_settings = _child_settings(config, task, share)

        child_registry = registry_factory(child_settings)
        sub_config     = SubagentConfig(
            agent_id  = agent_id,
            name      = task.subagent_type,
            prompt    = task.prompt,
            settings  = child_settings,
            registry  = child_registry,
            max_turns = config.max_turns,
            confirm   = label_confirm(config.confirm, agent_id),
            category  = task.category,
            parent_session_id = config.parent_session_id,
            absorb    = config.absorb,
            factories = config.factories,
        )

        try:
            output, _          = await run_subagent(sub_config, abort)
            spent.append(sub_config.cost_usd)
            task_state.status  = TaskStatus.COMPLETED
            task_state.output  = output
            return agent_id, output
        except Exception as exc:
            task_state.status = TaskStatus.FAILED
            task_state.error  = str(exc)
            return agent_id, f"[swarm error] {exc}"

    try:
        results_list = await asyncio.gather(*[_run_one(t) for t in config.tasks])
    finally:
        if share is not None:
            share[1].settle(share[0], sum(spent))
    return dict(results_list)


def _child_settings(config: SwarmConfig, task: SwarmTask, share: tuple[Envelope, Budget] | None) -> NerdvanaSettings:
    """The settings a worker runs with: the turn limit, its share of the cost limit and its model."""
    child = copy.deepcopy(config.settings)
    child.session.max_turns = config.max_turns
    if share is not None:
        child.session.max_cost_usd = share[0].amount / len(config.tasks)
    apply_model_spec(child, select_model(task.model, task.category, "", "", child.agents.categories))
    return child


def _reserve_share(config: SwarmConfig) -> tuple[Envelope, Budget] | None:
    """Set aside the swarm's share of the leader's cost limit, to be split between its tasks."""
    fraction = config.settings.session.subagent_budget_fraction
    if config.budget is None or config.budget[0].limit <= 0 or fraction <= 0 or not config.tasks:
        return None
    budget, own_spend = config.budget
    return budget.reserve(fraction, own_spend()), budget
