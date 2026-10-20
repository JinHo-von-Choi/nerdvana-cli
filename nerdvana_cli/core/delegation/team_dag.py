"""The shared task graph of a team: who waits for whom, and who may write what.

Task 3.1 of the Harness2/DSH enhancement plan.

Several agents write in the same repository at the same time. Two of them
touching the same files is a race that no amount of care afterwards repairs,
so the team keeps a graph of its work: which task waits for which, and which
paths each task may write. The graph answers two questions before a task
starts — may it run yet, and would it step on another's feet — and it refuses
the changes that would make either answer wrong.

Every change to the graph is a compare-and-set on the revision of the task it
changes: an agent that acts on a task another agent has already moved is
refused, not merged.

Based on DeepSeek Harness (``docs/subsystems/agent-team.md``).

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from nerdvana_cli.core.delegation.task_state import TaskStatus


class CasConflictError(RuntimeError):
    """The task moved while the change was being prepared."""


class CycleDetectedError(RuntimeError):
    """The edges would make the tasks wait for each other."""


@dataclass(frozen=True)
class TeamTask:
    """One task of the team: what it does, what it waits for, what it may write."""

    task_id:      str
    subject:      str
    status:       TaskStatus = TaskStatus.PENDING
    owner_id:     str        = ""
    blocked_by:   tuple[str, ...] = ()
    write_scopes: tuple[str, ...] = ()
    revision:     int        = 0


@dataclass(frozen=True)
class TaskView:
    """What a reader sees of a task: its state and what it may not do yet."""

    task_id:             str
    subject:             str
    status:              TaskStatus
    owner_id:            str
    blocked_by:          tuple[str, ...]
    write_scopes:        tuple[str, ...]
    revision:            int
    ready:               bool
    write_scope_warnings: tuple[str, ...] = ()


def scopes_overlap(first: str, second: str) -> bool:
    """True when two write scopes cover the same path, one of them inside the other."""
    left  = first.rstrip("/")
    right = second.rstrip("/")
    return left == right or right.startswith(f"{left}/") or left.startswith(f"{right}/")


class TeamTaskDAG:
    """The tasks of a team, the edges between them and the paths they may write."""

    def __init__(self) -> None:
        self._tasks: dict[str, TeamTask] = {}
        self._order: list[str]            = []

    # -- the graph --------------------------------------------------------

    def create_task(
        self,
        subject:      str,
        blocked_by:   Sequence[str] = (),
        write_scopes: Sequence[str] = (),
        task_id:      str           = "",
    ) -> TeamTask:
        """Add a task to the graph and return it."""
        task = TeamTask(
            task_id      = task_id or f"task-{len(self._order) + 1}",
            subject      = subject,
            blocked_by   = tuple(blocked_by),
            write_scopes = tuple(write_scopes),
        )
        if task.task_id in self._tasks:
            raise ValueError(f"Duplicate task id: {task.task_id}")
        self._tasks[task.task_id] = task
        self._order.append(task.task_id)
        return task

    def get(self, task_id: str) -> TeamTask:
        """The task under *task_id*."""
        if task_id not in self._tasks:
            raise KeyError(f"Unknown task id: {task_id}")
        return self._tasks[task_id]

    def views(self) -> list[TaskView]:
        """What every task looks like from the outside, in the order they were created."""
        warnings = self._write_scope_warnings()
        return [
            TaskView(
                task_id              = task.task_id,
                subject              = task.subject,
                status               = task.status,
                owner_id             = task.owner_id,
                blocked_by           = task.blocked_by,
                write_scopes         = task.write_scopes,
                revision             = task.revision,
                ready                = self._is_ready(task),
                write_scope_warnings = tuple(warnings.get(task.task_id, ())),
            )
            for task in (self._tasks[task_id] for task_id in self._order)
        ]

    # -- the changes ------------------------------------------------------

    def update_status(
        self,
        task_id:          str,
        expected_revision: int,
        status:           TaskStatus | str,
        owner_id:         str = "",
    ) -> TeamTask:
        """Move a task to another status, if it did not move in the meantime."""
        task    = self._checked(task_id, expected_revision)
        updated = replace(
            task,
            status   = TaskStatus(status),
            owner_id = owner_id or task.owner_id,
            revision = task.revision + 1,
        )
        self._tasks[task_id] = updated
        return updated

    def update_blockers(
        self,
        task_id:          str,
        expected_revision: int,
        blocked_by:       Sequence[str],
    ) -> TeamTask:
        """Change the tasks a task waits for, if the edges would not make them wait for each other."""
        task     = self._checked(task_id, expected_revision)
        blockers = tuple(blocked_by)
        for blocker in blockers:
            if blocker not in self._tasks:
                raise KeyError(f"Unknown task id: {blocker}")
        if self._would_cycle(task_id, blockers):
            raise CycleDetectedError(f"Task {task_id} would wait for itself through {', '.join(blockers)}")
        updated = replace(task, blocked_by=blockers, revision=task.revision + 1)
        self._tasks[task_id] = updated
        return updated

    # -- what the graph says ----------------------------------------------

    def ready_tasks(self) -> list[TeamTask]:
        """The tasks that may run now: nothing they wait for is still open."""
        return [task for task in self._tasks.values() if self._is_ready(task)]

    def waves(self) -> list[list[TeamTask]]:
        """The tasks in the order they may run: one wave of ready tasks at a time.

        A task waits for the wave before the one it is in, so the tasks of a
        wave are the ones that do not wait for each other.
        """
        remaining = [
            task_id for task_id in self._order
            if not self._is_finished(self._tasks[task_id])
        ]
        done = {
            task_id for task_id in self._order
            if self._is_finished(self._tasks[task_id])
        }
        waves: list[list[TeamTask]] = []
        while remaining:
            ready = [
                task_id for task_id in remaining
                if all(blocker in done for blocker in self._tasks[task_id].blocked_by)
            ]
            if not ready:
                raise CycleDetectedError("The tasks wait for each other; no task can run")
            waves.append([self._tasks[task_id] for task_id in ready])
            done.update(ready)
            remaining = [task_id for task_id in remaining if task_id not in ready]
        return waves

    def write_scope_warnings(self) -> dict[str, list[str]]:
        """The tasks whose write paths meet another task's, and why."""
        return self._write_scope_warnings()

    # -- the inside --------------------------------------------------------

    def _checked(self, task_id: str, expected_revision: int) -> TeamTask:
        """The task, if it still stands at the revision the caller expected."""
        task = self.get(task_id)
        if task.revision != expected_revision:
            raise CasConflictError(
                f"Task {task_id} is at revision {task.revision}, not {expected_revision}"
            )
        return task

    def _is_ready(self, task: TeamTask) -> bool:
        """True when nothing the task waits for is still open."""
        if self._is_finished(task):
            return False
        return all(
            blocker in self._tasks
            and self._tasks[blocker].status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.KILLED}
            for blocker in task.blocked_by
        )

    @staticmethod
    def _is_finished(task: TeamTask) -> bool:
        """True when the task will not run again."""
        return task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.KILLED}

    def _would_cycle(self, task_id: str, blockers: Iterable[str]) -> bool:
        """True when *task_id* is already reachable from the tasks it would wait for."""
        stack = list(blockers)
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            if current == task_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            task = self._tasks.get(current)
            if task is not None:
                stack.extend(task.blocked_by)
        return False

    def _write_scope_warnings(self) -> dict[str, list[str]]:
        """For every task that may still write, the paths it shares with another."""
        warnings: dict[str, list[str]] = {}
        open_tasks = [
            task for task in self._tasks.values()
            if task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.KILLED}
        ]
        for index, task in enumerate(open_tasks):
            for other in open_tasks[index + 1:]:
                for scope in task.write_scopes:
                    for other_scope in other.write_scopes:
                        if not scopes_overlap(scope, other_scope):
                            continue
                        warnings.setdefault(task.task_id, []).append(
                            f"'{scope}' overlaps with task {other.task_id} ('{other_scope}')"
                        )
                        warnings.setdefault(other.task_id, []).append(
                            f"'{other_scope}' overlaps with task {task.task_id} ('{scope}')"
                        )
        return warnings
