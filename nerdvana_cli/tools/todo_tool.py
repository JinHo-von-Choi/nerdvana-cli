"""TodoWrite tool — session-scoped task list persistence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, ClassVar

from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolSideEffect
from nerdvana_cli.types import ToolResult

_VALID_STATUSES = frozenset({"pending", "in_progress", "completed"})

# Reject any session_id that would escape the todos directory.
# Allow only alphanumeric, hyphen, underscore, and dot (no path separators).
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _sanitize_session_id(raw: str) -> str:
    """Return a filesystem-safe session identifier.

    If *raw* contains path-traversal sequences or disallowed characters the
    fallback ``"default"`` is returned instead.  This prevents a malicious or
    malformed session_id (e.g. ``"../../../etc"``) from writing outside the
    todos directory.
    """
    if raw and _SESSION_ID_RE.match(raw):
        return raw
    return "default"


def _todos_dir() -> Path:
    """Return ``~/.nerdvana/todos/``, creating it when absent."""
    d = Path.home() / ".nerdvana" / "todos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _validate_todos(todos: list[Any]) -> str | None:
    """Return an error message when *todos* fails schema validation, else None."""
    if not isinstance(todos, list):
        return "todos must be an array"

    for idx, item in enumerate(todos):
        if not isinstance(item, dict):
            return f"todos[{idx}] must be an object"

        for required_key in ("content", "status", "activeForm"):
            if required_key not in item:
                return f"todos[{idx}] missing required field '{required_key}'"

        status = item["status"]
        if status not in _VALID_STATUSES:
            return (
                f"todos[{idx}].status '{status}' is not one of "
                f"{sorted(_VALID_STATUSES)}"
            )

        if not isinstance(item["content"], str):
            return f"todos[{idx}].content must be a string"

        if not isinstance(item["activeForm"], str):
            return f"todos[{idx}].activeForm must be a string"

    return None


class TodoWriteArgs:
    def __init__(self, todos: list[dict[str, Any]]) -> None:
        self.todos = todos


class TodoWriteTool(BaseTool[TodoWriteArgs]):
    name             = "TodoWrite"
    description_text = """Persist a task list for the current session.
Each todo has content (description), status (pending|in_progress|completed),
and activeForm (verb form for in-progress display). Idempotent: re-passing
the same todo list overwrites the previous state."""
    input_schema = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content":    {"type": "string"},
                        "status":     {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                        "activeForm": {"type": "string"},
                    },
                    "required": ["content", "status", "activeForm"],
                },
            },
        },
        "required": ["todos"],
    }

    is_concurrency_safe                    = False
    args_class                             = TodoWriteArgs
    category:     ClassVar[ToolCategory]   = ToolCategory.WRITE
    side_effects: ClassVar[ToolSideEffect] = ToolSideEffect.FILESYSTEM
    tags:         ClassVar[frozenset[str]] = frozenset({"task", "todo"})
    requires_confirmation                  = False

    async def call(
        self,
        args: TodoWriteArgs,
        context: ToolContext,
        can_use_tool: Any  = None,
        on_progress: Any   = None,
    ) -> ToolResult:
        err = _validate_todos(args.todos)
        if err:
            return ToolResult(tool_use_id="", content=err, is_error=True)

        raw_session_id: str = context.state.get("session_id", "") or ""
        session_id          = _sanitize_session_id(raw_session_id) if raw_session_id else "default"

        todos_dir   = _todos_dir()
        target_path = todos_dir / f"{session_id}.json"

        try:
            target_path.write_text(
                json.dumps({"todos": args.todos}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            return ToolResult(
                tool_use_id="",
                content=f"Failed to write todos: {exc}",
                is_error=True,
            )

        payload = {
            "todos":    args.todos,
            "saved_to": str(target_path),
        }
        return ToolResult(tool_use_id="", content=json.dumps(payload, ensure_ascii=False))
