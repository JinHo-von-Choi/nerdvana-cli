"""AgentTool — spawns a subagent (isolated AgentLoop) to handle a subtask."""

from __future__ import annotations

import asyncio
import copy
import uuid
from dataclasses import dataclass
from typing import Any

from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.subagent import SubagentConfig, run_subagent
from nerdvana_cli.core.task_state import TaskRegistry, TaskState, TaskStatus
from nerdvana_cli.core.tool import BaseTool, ToolContext
from nerdvana_cli.types import ToolResult


@dataclass
class AgentToolArgs:
    prompt:            str
    description:       str  = ""
    subagent_type:     str  = "general-purpose"
    model:             str  = ""
    run_in_background: bool = False


class AgentTool(BaseTool[AgentToolArgs]):
    """Spawn a subagent to handle a complex, multi-step task independently."""

    name             = "Agent"
    description_text = (
        "Spawn a subagent to handle a complex, multi-step task independently. "
        "The subagent runs in isolation with its own context window and returns "
        "its output when complete. Use run_in_background=true for fire-and-forget "
        "tasks that you will poll with TaskGet later."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Short (3-5 word) description of what this agent will do.",
            },
            "prompt": {
                "type": "string",
                "description": "Full task prompt for the subagent.",
            },
            "subagent_type": {
                "type": "string",
                "description": (
                    "Agent type: general-purpose, Explore, Plan, "
                    "code-reviewer, git-management, test-writer, "
                    "or any custom type from .nerdvana/agents/"
                ),
                "default": "general-purpose",
            },
            "model": {
                "type": "string",
                "description": "Optional model override (empty = inherit parent model).",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "If true, returns task_id immediately without waiting.",
                "default": False,
            },
        },
        "required": ["prompt"],
    }
    is_concurrency_safe = True
    args_class          = AgentToolArgs

    def __init__(
        self,
        settings:      NerdvanaSettings,
        task_registry: TaskRegistry,
    ) -> None:
        self._settings      = settings
        self._task_registry = task_registry

    async def call(
        self,
        args:         AgentToolArgs,
        context:      ToolContext,
        can_use_tool: Any,
        on_progress:  Any = None,
    ) -> ToolResult:
        task_id  = f"agent_{uuid.uuid4().hex[:8]}"
        abort    = asyncio.Event()
        task     = TaskState(
            id          = task_id,
            description = args.description or args.prompt[:80],
            status      = TaskStatus.RUNNING,
            abort       = abort,
        )
        registry = context.task_registry or self._task_registry
        registry.register(task)

        child_settings = copy.deepcopy(self._settings)
        if args.model:
            child_settings.model.model = args.model
        child_settings.session.max_turns = 50

        import os

        from nerdvana_cli.agents.builtin import BUILTIN_AGENTS
        from nerdvana_cli.agents.registry import AgentTypeRegistry
        from nerdvana_cli.tools.registry import create_subagent_registry

        _agent_type_reg = AgentTypeRegistry()
        for defn in BUILTIN_AGENTS:
            _agent_type_reg.register(defn)
        _agent_type_reg.load_from_dir(
            os.path.join(os.getcwd(), ".nerdvana", "agents")
        )

        agent_defn = _agent_type_reg.get(args.subagent_type)
        if agent_defn is None:
            available = ", ".join(sorted(_agent_type_reg._agents.keys()))
            task.status = TaskStatus.FAILED
            task.error = f"Unknown agent type: {args.subagent_type}"
            return ToolResult(
                tool_use_id="",
                content=f"Unknown agent type: '{args.subagent_type}'. Available: {available}",
                is_error=True,
            )
        allowed_tools = agent_defn.allowed_tools
        child_registry = create_subagent_registry(
            settings      = child_settings,
            allowed_tools = allowed_tools,
        )
        config = SubagentConfig(
            agent_id = task_id,
            name     = args.subagent_type,
            prompt   = args.prompt,
            settings = child_settings,
            registry = child_registry,
        )

        if args.run_in_background:
            bg = asyncio.get_event_loop().create_task(
                self._run_and_record(config, task, abort)
            )
            task.bg_task = bg
            return ToolResult(
                tool_use_id = "",
                content     = f"Agent started in background. Task ID: {task_id}",
            )

        output = await self._run_and_record(config, task, abort)
        return ToolResult(tool_use_id="", content=output)

    async def _run_and_record(
        self,
        config: SubagentConfig,
        task:   TaskState,
        abort:  asyncio.Event,
    ) -> str:
        try:
            output      = await run_subagent(config, abort)
            task.status = TaskStatus.COMPLETED
            task.output = output
            return output
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error  = str(exc)
            return f"[agent error] {exc}"
