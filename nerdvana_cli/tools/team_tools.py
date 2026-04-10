"""Team tools: TeamCreate, SendMessage, TaskGet, TaskStop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nerdvana_cli.core.task_state import TaskRegistry, TaskStatus
from nerdvana_cli.core.team import (
    TeammateMessage,
    TeamRegistry,
    get_inbox_path,
    write_to_inbox,
)
from nerdvana_cli.core.tool import BaseTool, ToolContext
from nerdvana_cli.types import ToolResult

# ---------------------------------------------------------------------------
# TeamCreate
# ---------------------------------------------------------------------------

@dataclass
class TeamCreateArgs:
    team_name:   str
    description: str = ""


class TeamCreateTool(BaseTool[TeamCreateArgs]):
    """Create a named multi-agent team."""

    name             = "TeamCreate"
    description_text = "Create a named team for multi-agent coordination."
    input_schema     = {
        "type": "object",
        "properties": {
            "team_name":   {"type": "string", "description": "Unique team name."},
            "description": {"type": "string", "description": "Team purpose."},
        },
        "required": ["team_name"],
    }
    is_concurrency_safe = True
    args_class          = TeamCreateArgs

    def __init__(self, team_registry: TeamRegistry) -> None:
        self._team_registry = team_registry

    async def call(
        self,
        args:         TeamCreateArgs,
        context:      ToolContext,
        can_use_tool: Any,
        on_progress:  Any = None,
    ) -> ToolResult:
        registry = context.team_registry or self._team_registry
        registry.create(args.team_name)
        return ToolResult(
            tool_use_id = "",
            content     = f"Team '{args.team_name}' created.",
        )


# ---------------------------------------------------------------------------
# SendMessage
# ---------------------------------------------------------------------------

@dataclass
class SendMessageArgs:
    to:        str
    message:   str
    team_name: str = ""
    summary:   str = ""


class SendMessageTool(BaseTool[SendMessageArgs]):
    """Send a message to a teammate's mailbox."""

    name             = "SendMessage"
    description_text = "Send a text message to a named teammate in the team."
    input_schema     = {
        "type": "object",
        "properties": {
            "to":        {"type": "string", "description": "Recipient agent name."},
            "message":   {"type": "string", "description": "Message content."},
            "team_name": {"type": "string", "description": "Team the recipient belongs to."},
            "summary":   {"type": "string", "description": "5-10 word preview summary."},
        },
        "required": ["to", "message"],
    }
    is_concurrency_safe = True
    args_class          = SendMessageArgs

    def __init__(
        self,
        team_registry: TeamRegistry,
        base_dir:      str = "",
    ) -> None:
        self._team_registry = team_registry
        self._base_dir      = base_dir

    async def call(
        self,
        args:         SendMessageArgs,
        context:      ToolContext,
        can_use_tool: Any,
        on_progress:  Any = None,
    ) -> ToolResult:
        inbox = get_inbox_path(args.to, args.team_name or "default", base_dir=self._base_dir)
        msg   = TeammateMessage(
            from_agent = "leader",
            text       = args.message,
            summary    = args.summary,
        )
        await write_to_inbox(inbox, msg)
        return ToolResult(
            tool_use_id = "",
            content     = f"Message sent to '{args.to}'.",
        )


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
    is_concurrency_safe = True
    args_class          = TaskGetArgs

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
    is_concurrency_safe = True
    args_class          = TaskStopArgs

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
