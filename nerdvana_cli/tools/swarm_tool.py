"""SwarmTool — model-callable entry point for agent swarm execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.swarm import SwarmConfig, SwarmTask, run_swarm
from nerdvana_cli.core.task_state import TaskRegistry
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult


@dataclass
class SwarmToolArgs:
    team_name: str
    tasks:     list[dict[str, str]]
    max_turns: int = 50


class SwarmTool(BaseTool[SwarmToolArgs]):
    """Dispatch multiple agents in parallel and aggregate results (swarm pattern)."""

    name             = "Swarm"
    description_text = (
        "Dispatch multiple independent subagents in parallel to solve parts of a "
        "complex problem. All agents run concurrently; results are collected and "
        "returned together. Use this when tasks are fully independent."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "team_name": {
                "type": "string",
                "description": "Logical team name for this swarm run.",
            },
            "tasks": {
                "type": "array",
                "description": "List of task objects with 'name' and 'prompt' keys.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":          {"type": "string"},
                        "prompt":        {"type": "string"},
                        "subagent_type": {"type": "string"},
                        "model":         {"type": "string"},
                    },
                    "required": ["name", "prompt"],
                },
            },
            "max_turns": {
                "type": "integer",
                "description": "Max turns per agent (default 50).",
                "default": 50,
            },
        },
        "required": ["team_name", "tasks"],
    }
    is_concurrency_safe    = True
    args_class             = SwarmToolArgs
    category               = ToolCategory.META
    side_effects           = ToolSideEffect.EXTERNAL
    tags: ClassVar[frozenset[str]] = frozenset({"agent", "concurrent"})
    requires_confirmation  = False

    def __init__(
        self,
        settings:      NerdvanaSettings,
        task_registry: TaskRegistry,
    ) -> None:
        self._settings      = settings
        self._task_registry = task_registry

    async def call(
        self,
        args:         SwarmToolArgs,
        context:      ToolContext,
        can_use_tool: Any,
        on_progress:  Any = None,
    ) -> ToolResult:
        registry    = context.task_registry or self._task_registry
        swarm_tasks = [
            SwarmTask(
                name          = t["name"],
                prompt        = t["prompt"],
                subagent_type = t.get("subagent_type", "general-purpose"),
                model         = t.get("model", ""),
            )
            for t in args.tasks
        ]
        config = SwarmConfig(
            team_name     = args.team_name,
            tasks         = swarm_tasks,
            settings      = self._settings,
            task_registry = registry,
            max_turns     = args.max_turns,
        )
        results = await run_swarm(config)

        lines = [f"Swarm '{args.team_name}' completed. {len(results)} agents ran.\n"]
        for agent_id, output in results.items():
            lines.append(f"### {agent_id}\n{output}\n")

        return ToolResult(tool_use_id="", content="\n".join(lines))
