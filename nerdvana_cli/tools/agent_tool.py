"""AgentTool — spawns a subagent (isolated AgentLoop) to handle a subtask."""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from nerdvana_cli.core.config.model_routing import apply_model_spec, select_model
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.delegation.subagent import label_confirm, run_subagent
from nerdvana_cli.core.delegation.task_state import TaskRegistry, TaskState, TaskStatus
from nerdvana_cli.core.delegation.worktree import (
    Worktree,
    WorktreeError,
    create_worktree,
    git_dirs,
    has_changes,
    remove_worktree,
)
from nerdvana_cli.core.loop.subagent_config import SubagentConfig
from nerdvana_cli.core.safety.agent_scope import apply_write_scope
from nerdvana_cli.core.state.run_store import FAILED, STOPPED, SUCCEEDED, TaskRecorder
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolRegistry, ToolSideEffect
from nerdvana_cli.tools.subagent_registry import create_subagent_registry
from nerdvana_cli.types import ToolResult

# How a task's final state is written to its run record; a task cancelled with its process counts as stopped.
_RECORD_STATUS = {TaskStatus.COMPLETED: SUCCEEDED, TaskStatus.FAILED: FAILED, TaskStatus.KILLED: STOPPED}


@dataclass
class AgentToolArgs:
    prompt:            str
    description:       str  = ""
    subagent_type:     str  = "general-purpose"
    model:             str  = ""
    category:          str  = ""
    isolation:         str  = ""
    run_in_background: bool = False


def open_envelope(context: ToolContext, child_settings: NerdvanaSettings) -> Callable[[float], None] | None:
    """Give a sub-agent its share of the parent's cost limit; returns the call that settles it afterwards.

    Nothing is reserved when the parent has no cost limit or ``session.subagent_budget_fraction`` is 0.
    """
    shared = context.state.get("budget")
    fraction = child_settings.session.subagent_budget_fraction
    if shared is None or shared[0].limit <= 0 or fraction <= 0:
        return None
    budget, own_spend = shared
    envelope = budget.reserve(fraction, own_spend())
    child_settings.session.max_cost_usd = envelope.amount
    return lambda actual: budget.settle(envelope, actual)


def enter_worktree(args: AgentToolArgs, task_id: str, child_settings: NerdvanaSettings, context: ToolContext) -> Worktree | None:
    """With ``isolation: worktree``, make the agent's checkout and point its settings at it; None otherwise."""
    if args.isolation != "worktree":
        return None
    worktree = create_worktree(context.cwd, args.description or task_id)
    child_settings.cwd = worktree.path
    child_settings.sandbox.write_paths = [*child_settings.sandbox.write_paths, *git_dirs(worktree)]
    return worktree


def leave_worktree(worktree: Worktree | None) -> str:
    """A note for the agent's result: where its changes are, or nothing (the unchanged worktree is removed)."""
    if worktree is None:
        return ""
    try:
        if has_changes(worktree):
            return (
                f"\n\n[This agent worked in its own git worktree. Its changes are on branch {worktree.branch} in {worktree.path}. "
                f"Review them with: git -C {worktree.path} diff HEAD; to keep them, commit there and merge the branch.]"
            )
        remove_worktree(worktree)
    except WorktreeError as exc:
        return f"\n\n[Worktree {worktree.path} could not be checked or removed: {exc}]"
    return "\n\n[The agent's worktree had no changes and was removed.]"


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
        "tasks that you will poll with TaskGet later. The subagent does not see this conversation, so the prompt "
        "must carry every fact it needs. "
        "Example: description: \"Find token callers\", subagent_type: \"Explore\", "
        "prompt: \"List every caller of verify_token under src/ with file:line. Do not edit files.\"."
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
            "isolation": {
                "type": "string",
                "enum": ["", "worktree"],
                "description": (
                    "'worktree' runs the agent in its own git worktree on a new branch, so its edits do not touch "
                    "the project directory; the result says where the changes are if there are any."
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
        settle         = open_envelope(context, child_settings)
        try:
            worktree = enter_worktree(args, task_id, child_settings, context)
        except WorktreeError as exc:
            task.status, task.error = TaskStatus.FAILED, str(exc)
            return ToolResult(tool_use_id="", content=str(exc), is_error=True)

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
        apply_write_scope(child_settings, agent_defn, context.cwd)
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
            absorb        = context.state.get("absorb"),
            factories     = context.state.get("loop_factories"),
        )

        if args.run_in_background:
            return self._start_background(args, context, config, task, registry, settle, worktree)

        output, total_tokens = await self._run_and_record(config, task, abort, registry, settle, worktree)
        return ToolResult(tool_use_id="", content=output, tokens=total_tokens)

    def _start_background(
        self,
        args:     AgentToolArgs,
        context:  ToolContext,
        config:   SubagentConfig,
        task:     TaskState,
        registry: TaskRegistry,
        settle:   Callable[[float], None] | None,
        worktree: Worktree | None,
    ) -> ToolResult:
        """Run the agent as a task of its own, with a durable record (core/state/run_store.py) that outlives this process."""
        task.background = True
        recorder        = TaskRecorder.start(task.id, args.prompt, context.cwd, worktree)
        task.bg_task    = asyncio.get_event_loop().create_task(
            self._run_in_background(recorder, config, task, registry, settle, worktree)
        )
        return ToolResult(tool_use_id="", content=f"Agent started in background. Task ID: {task.id}")

    async def _run_in_background(
        self,
        recorder: TaskRecorder | None,
        config:   SubagentConfig,
        task:     TaskState,
        registry: TaskRegistry,
        settle:   Callable[[float], None] | None,
        worktree: Worktree | None,
    ) -> tuple[str, int]:
        """Run the agent and keep its record: the lease renewed while it runs, the outcome written when it ends."""
        lease = asyncio.get_event_loop().create_task(recorder.keep_lease()) if recorder else None
        try:
            return await self._run_and_record(config, task, task.abort, registry, settle, worktree)
        finally:
            if lease is not None and recorder is not None:
                lease.cancel()
                recorder.finish(_RECORD_STATUS.get(task.status, STOPPED), task.output, task.error or "", config.cost_usd)

    async def _run_and_record(
        self,
        config:   SubagentConfig,
        task:     TaskState,
        abort:    asyncio.Event,
        registry: TaskRegistry,
        settle:   Callable[[float], None] | None = None,
        worktree: Worktree | None = None,
    ) -> tuple[str, int]:
        try:
            output, total_tokens = await run_subagent(config, abort)
            output += leave_worktree(worktree)
            task.status = TaskStatus.COMPLETED
            task.output = output
            return output, total_tokens
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error  = str(exc)
            return f"[agent error] {exc}", 0
        finally:
            if settle is not None:
                settle(config.cost_usd)
            registry.mark_finished(task)
