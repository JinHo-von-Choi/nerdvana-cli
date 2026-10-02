"""Background task tools: TaskGet, TaskStop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from nerdvana_cli.core.task_state import TaskRegistry, TaskStatus
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult

# ---------------------------------------------------------------------------
# TaskGet
# ---------------------------------------------------------------------------

@dataclass
class TaskGetArgs:
    task_id: str


class TaskGetTool(BaseTool[TaskGetArgs]):
    """Get the status and output of a background agent task."""

    name             = "TaskGet"
    description_text = "Check the status and output of a background agent task by task_id."
    input_schema     = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task ID returned by Agent(run_in_background=true)."},
        },
        "required": ["task_id"],
    }
    is_concurrency_safe    = True
    args_class             = TaskGetArgs
    category               = ToolCategory.META
    side_effects           = ToolSideEffect.NONE
    tags: ClassVar[frozenset[str]] = frozenset({"agent", "introspect"})
    requires_confirmation  = False

    def __init__(self, task_registry: TaskRegistry) -> None:
        self._task_registry = task_registry

    async def call(
        self,
        args:         TaskGetArgs,
        context:      ToolContext,
        can_use_tool: Any,
        on_progress:  Any = None,
    ) -> ToolResult:
        registry = context.task_registry or self._task_registry
        task     = registry.get(args.task_id)
        if task is None:
            return ToolResult(
                tool_use_id = "",
                content     = f"Task '{args.task_id}' not found.",
                is_error    = True,
            )
        lines = [
            f"task_id: {task.id}",
            f"status:  {task.status}",
            f"description: {task.description}",
        ]
        if task.output:
            lines.append(f"\n--- output ---\n{task.output}")
        if task.error:
            lines.append(f"\n--- error ---\n{task.error}")
        return ToolResult(tool_use_id="", content="\n".join(lines))


# ---------------------------------------------------------------------------
# TaskStop
# ---------------------------------------------------------------------------

@dataclass
class TaskStopArgs:
    task_id: str
    reason:  str = ""


class TaskStopTool(BaseTool[TaskStopArgs]):
    """Stop a running background agent task."""

    name             = "TaskStop"
    description_text = "Cancel a running background agent task."
    input_schema     = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task ID to stop."},
            "reason":  {"type": "string", "description": "Reason for stopping."},
        },
        "required": ["task_id"],
    }
    is_concurrency_safe    = True
    args_class             = TaskStopArgs
    category               = ToolCategory.META
    side_effects           = ToolSideEffect.EXTERNAL
    tags: ClassVar[frozenset[str]] = frozenset({"agent"})
    requires_confirmation  = True

    def __init__(self, task_registry: TaskRegistry) -> None:
        self._task_registry = task_registry

    async def call(
        self,
        args:         TaskStopArgs,
        context:      ToolContext,
        can_use_tool: Any,
        on_progress:  Any = None,
    ) -> ToolResult:
        registry = context.task_registry or self._task_registry
        task     = registry.get(args.task_id)
        if task is None:
            return ToolResult(
                tool_use_id = "",
                content     = f"Task '{args.task_id}' not found.",
                is_error    = True,
            )
        if task.status != TaskStatus.RUNNING:
            return ToolResult(
                tool_use_id = "",
                content     = f"Task '{args.task_id}' is not running (status: {task.status}).",
            )
        task.abort.set()
        task.status = TaskStatus.KILLED
        if task.bg_task and not task.bg_task.done():
            task.bg_task.cancel()
        return ToolResult(
            tool_use_id = "",
            content     = f"Task '{args.task_id}' stopped.",
        )
