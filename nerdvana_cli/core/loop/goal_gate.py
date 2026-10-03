"""The goal a session is held to, and the verification that decides whether the run may end.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from rich.markup import escape

from nerdvana_cli.core.loop.auto_verify import detect_test_command
from nerdvana_cli.core.loop.loop_state import LoopFlow
from nerdvana_cli.core.loop.phase_effort import VERIFICATION
from nerdvana_cli.core.loop.verify import run_verify
from nerdvana_cli.core.state import signals
from nerdvana_cli.core.state.goal import MET, UNMET, Goal, load_goal, save_goal
from nerdvana_cli.types import Message, Role

if TYPE_CHECKING:
    from nerdvana_cli.core.loop.agent_loop import AgentLoop

_VERIFY_FAILED = (
    "[Verification] `{command}` did not pass ({summary}, attempt {attempt} of {limit}). The end of its output:\n\n"
    "{tail}\n\n"
    "The objective is not met until that command exits with status 0. Find the cause and fix it. "
    "Do not change the verification command or weaken the checks it runs to make it pass."
)


class GoalGate:
    """Holds the run of *loop* to its goal: the session's own, else the project's tests after an edit."""

    def __init__(self, loop: AgentLoop) -> None:
        self._loop                = loop
        self._goal:   Goal | None = None
        self._loaded              = False
        self._auto:   Goal | None = None
        self._edit_mark           = 0

    @property
    def goal(self) -> Goal | None:
        """The goal this session is held to, loaded from its file the first time it is asked for."""
        if not self._loaded:
            self._goal   = load_goal(self._loop.session.session_id)
            self._loaded = True
        return self._goal

    def set_goal(self, goal: Goal | None) -> None:
        """Hold the session to *goal* (None drops it) and save the change."""
        self._goal   = goal
        self._loaded = True
        save_goal(self._loop.session.session_id, goal)

    def scope(self) -> list[str] | None:
        """The paths an enforced goal is about, or None when edits are not confined."""
        goal = self.goal
        return goal.scope if goal is not None and goal.enforced and goal.scope else None

    def summary(self) -> dict[str, Any] | None:
        """How the goal stands, for the run result; None when the session has no goal."""
        goal = self.goal or self._auto
        if goal is None:
            return None
        return {"command": goal.verify, "status": goal.status, "attempts": goal.attempts, "last_exit": goal.last_exit}

    def start_run(self) -> None:
        """Forget the detected-test check of an earlier run and count edits from here on."""
        self._auto      = None
        self._edit_mark = self._edit_count()

    def _edit_count(self) -> int:
        """How many edits the edit tools have applied in this session so far."""
        return sum(self._loop.tool_executor.edited.values())

    def completion_goal(self) -> Goal | None:
        """The goal that must be met before the run may end: the session's own, else the detected-test check.

        Without a goal and with ``goal.auto_verify`` on, a run that has changed files since the last
        passing check is held to the project's test command; none detected means no check.
        """
        settings = self._loop.settings
        if self.goal is not None:
            return self.goal if self.goal.enforced else None
        if not settings.goal.auto_verify or self._edit_count() <= self._edit_mark:
            return None
        if self._auto is not None and self._auto.enforced:
            return self._auto
        command = detect_test_command(settings.cwd or ".")
        if not command:
            return None
        self._auto = Goal("Keep the project's tests passing", command, max_attempts=settings.goal.max_attempts)
        return self._auto

    async def verify(self, flow: LoopFlow, goal: Goal) -> AsyncGenerator[str, None]:
        """Run the verification command of *goal* now that the model says it is done.

        A pass ends the run; running out of attempts ends it as unmet; otherwise the failure is put in
        front of the model and the run goes on (``flow.finished`` stays False) at the verification effort.
        """
        loop   = self._loop
        config = loop.settings.goal
        yield f"\n[dim]Verifying: {escape(goal.verify)}[/dim]\n"
        result = await run_verify(
            goal.verify, loop.settings.cwd or ".", timeout=config.verify_timeout, tail=config.output_tail_chars,
            policy=loop._sandbox_policy(),
        )
        result = replace(result, tail=loop.tool_executor.mask_text(result.tail))
        goal.record_attempt(result.passed, result.exit_code, result.tail)
        if goal is self.goal:
            save_goal(loop.session.session_id, goal)
        if goal.status == MET:
            self._edit_mark = self._edit_count()
            yield f"[green]Goal met: {escape(goal.verify)} passed ({result.summary()}).[/green]\n"
            flow.finished = True
            return
        loop._signals[signals.VERIFY_FAILED] += 1
        if goal.status == UNMET:
            loop.last_stop = "goal_unmet"
            flow.finished  = True
            yield f"[bold yellow]Goal not met after {goal.attempts} verification attempts ({result.summary()}). Stopping.[/bold yellow]\n"
            return
        yield f"[yellow]Verification failed ({result.summary()}); the agent continues.[/yellow]\n"
        loop.phase_effort.enter(VERIFICATION)
        loop.state.messages.append(Message(role=Role.USER, content=_VERIFY_FAILED.format(
            command=goal.verify, summary=result.summary(), attempt=goal.attempts, limit=goal.max_attempts, tail=result.tail.strip(),
        )))
