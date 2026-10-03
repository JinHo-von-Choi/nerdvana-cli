"""AgentTool — spawns a subagent (isolated AgentLoop) to handle a subtask."""

from __future__ import annotations

import asyncio
import copy
import uuid
from dataclasses import dataclass
from typing import Any, ClassVar

from nerdvana_cli.core.model_routing import apply_model_spec, select_model
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.subagent import SubagentConfig, label_confirm, run_subagent
from nerdvana_cli.core.task_state import TaskRegistry, TaskState, TaskStatus
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolRegistry, ToolSideEffect
from nerdvana_cli.types import ToolResult


@dataclass
class AgentToolArgs:
    prompt:            str
    description:       str  = ""
    subagent_type:     str  = "general-purpose"
    model:             str  = ""
    category:          str  = ""
    run_in_background: bool = False


def _agent_types() -> Any:
    """The built-in agent types plus the ones defined under ``.nerdvana/agents`` of the working directory."""
    import os

    from nerdvana_cli.agents.builtin import BUILTIN_AGENTS
    from nerdvana_cli.agents.registry import AgentTypeRegistry

    registry = AgentTypeRegistry()
    for defn in BUILTIN_AGENTS:
        registry.register(defn)
    registry.load_from_dir(os.path.join(os.getcwd(), ".nerdvana", "agents"))
    return registry


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
                "description": (
                    "Optional model override, 'model' or 'provider:model' "
                    "(empty = the agent type's model, then its category, then the parent's)."
                ),
            },
            "category": {
                "type": "string",
                "description": (
                    "Optional task category mapped to a model by agents.categories "
                    "in the configuration (empty = the agent type's category)."
                ),
            },
            "run_in_background": {
                "type": "boolean",
                "description": "If true, returns task_id immediately without waiting.",
                "default": False,
            },
        },
        "required": ["prompt"],
    }
    is_concurrency_safe    = True
    args_class             = AgentToolArgs
    category               = ToolCategory.META
    side_effects           = ToolSideEffect.EXTERNAL
    tags: ClassVar[frozenset[str]] = frozenset({"agent"})
    requires_confirmation  = False

    def __init__(
        self,
        settings:        NerdvanaSettings,
        task_registry:   TaskRegistry,
        parent_registry: ToolRegistry | None = None,
    ) -> None:
        self._settings        = settings
        self._task_registry   = task_registry
        self._parent_registry = parent_registry

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

        from nerdvana_cli.tools.registry import create_subagent_registry

        _agent_type_reg = _agent_types()
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
        apply_model_spec(child_settings, select_model(args.model, args.category, agent_defn.model, agent_defn.category, child_settings.agents.categories))
        child_settings.session.max_turns = agent_defn.max_turns
        child_registry = create_subagent_registry(
            settings      = child_settings,
            allowed_tools = allowed_tools,
            parent_tools  = self._parent_registry.all_tools() if self._parent_registry else None,
        )
        config = SubagentConfig(
            agent_id      = task_id,
            name          = args.subagent_type,
            prompt        = args.prompt,
            settings      = child_settings,
            registry      = child_registry,
            max_turns     = agent_defn.max_turns,
            system_prompt = agent_defn.system_prompt,
            confirm       = label_confirm(context.confirm, task_id),
            category      = args.category or agent_defn.category,
            parent_session_id = str(context.state.get("session_id", "")),
        )

        if args.run_in_background:
            task.background = True
            bg = asyncio.get_event_loop().create_task(
                self._run_and_record(config, task, abort, registry)
            )
            task.bg_task = bg
            return ToolResult(
                tool_use_id = "",
                content     = f"Agent started in background. Task ID: {task_id}",
            )

        output, total_tokens = await self._run_and_record(config, task, abort, registry)
        return ToolResult(tool_use_id="", content=output, tokens=total_tokens)

    async def _run_and_record(
        self,
        config:   SubagentConfig,
        task:     TaskState,
        abort:    asyncio.Event,
        registry: TaskRegistry,
    ) -> tuple[str, int]:
        try:
            output, total_tokens = await run_subagent(config, abort)
            task.status = TaskStatus.COMPLETED
            task.output = output
            return output, total_tokens
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error  = str(exc)
            return f"[agent error] {exc}", 0
        finally:
            registry.mark_finished(task)
