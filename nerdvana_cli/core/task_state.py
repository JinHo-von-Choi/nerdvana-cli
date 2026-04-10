"""Task state and registry for subagent/team lifecycle tracking."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    KILLED    = "killed"


@dataclass
class TaskState:
    id:            str
    description:   str
    status:        TaskStatus        = TaskStatus.PENDING
    output:        str               = ""
    error:         str | None        = None
    tool_use_id:   str | None        = None
    abort:         asyncio.Event     = field(default_factory=asyncio.Event)
    bg_task:       asyncio.Task[Any] | None = field(default=None, repr=False)
    current_tool:  str               = ""
    tokens_used:   int               = 0
    output_buffer: list[str]         = field(default_factory=list)


class TaskRegistry:
    """In-memory registry of all live agent tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}

    def register(self, task: TaskState) -> None:
        self._tasks[task.id] = task

    def get(self, task_id: str) -> TaskState | None:
        return self._tasks.get(task_id)

    def all(self) -> list[TaskState]:
        return list(self._tasks.values())

    def running(self) -> list[TaskState]:
        return [t for t in self._tasks.values() if t.status == TaskStatus.RUNNING]

    def evict(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)
