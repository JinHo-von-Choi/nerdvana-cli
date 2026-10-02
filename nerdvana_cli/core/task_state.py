"""Task state and registry for subagent/team lifecycle tracking."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
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
    background:    bool              = False
    reported:      bool              = False
    finished_at:   float | None      = None


_TERMINAL = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.KILLED})

# Reported tasks are dropped this many seconds after they finished.
_FINISHED_TTL = 3600.0


class TaskRegistry:
    """In-memory registry of all live agent tasks."""

    def __init__(self) -> None:
        self._tasks:     dict[str, TaskState] = {}
        self._listeners: list[Callable[[TaskState], None]] = []

    def add_listener(self, listener: Callable[[TaskState], None]) -> None:
        """Call *listener* whenever a background task finishes."""
        self._listeners.append(listener)

    def mark_finished(self, task: TaskState) -> None:
        """Record that *task* reached a terminal state and tell the listeners."""
        task.finished_at = time.monotonic()
        if task.background:
            for listener in list(self._listeners):
                listener(task)

    def drain_unreported(self) -> list[TaskState]:
        """Finished background tasks nobody has seen yet; marks them seen."""
        fresh = [
            t for t in self._tasks.values()
            if t.background and not t.reported and t.status in _TERMINAL
        ]
        for task in fresh:
            task.reported = True
        self._evict_stale()
        return fresh

    def has_unreported(self) -> bool:
        """True when a finished background task has not been reported yet."""
        return any(t.background and not t.reported and t.status in _TERMINAL for t in self._tasks.values())

    def _evict_stale(self) -> None:
        now = time.monotonic()
        for task_id, task in list(self._tasks.items()):
            if task.reported and task.finished_at is not None and now - task.finished_at > _FINISHED_TTL:
                del self._tasks[task_id]

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
