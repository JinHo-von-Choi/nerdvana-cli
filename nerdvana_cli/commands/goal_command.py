"""The ``/goal`` command: hold the agent to a verification command.

Author: 최진호
Date:   2026-10-03

    /goal                         show the current goal
    /goal <objective> --verify <command> [--attempts N] [--scope PATH]...
                                  set a goal and start working on it; edits outside the scope ask first
    /goal pause | resume | clear  suspend, resume or drop the goal
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.markup import escape

from nerdvana_cli.core.goal import ACTIVE, PAUSED, Goal, GoalError

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp

USAGE = "Usage: /goal <objective> --verify <command> [--attempts N] [--scope PATH]   |   /goal pause|resume|clear   |   /goal"


@dataclass(frozen=True)
class GoalRequest:
    """What the user asked of ``/goal``."""

    action:    str            # show, pause, resume, clear, set
    objective: str = ""
    verify:    str = ""
    attempts:  int = 0
    scope:     tuple[str, ...] = ()


class GoalUsageError(ValueError):
    """The arguments cannot be read."""


def parse_goal_args(args: str) -> GoalRequest:
    """Read the text after ``/goal``. Quote a command that has spaces: ``--verify "pytest -q"``."""
    text = args.strip()
    if not text:
        return GoalRequest("show")
    if text in ("pause", "resume", "clear"):
        return GoalRequest(text)
    try:
        words = shlex.split(text)
    except ValueError as exc:
        raise GoalUsageError(f"cannot read the arguments: {exc}") from exc
    objective: list[str] = []
    verify, attempts = "", 0
    scope: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word in ("--verify", "--attempts", "--scope"):
            if index + 1 >= len(words):
                raise GoalUsageError(f"{word} needs a value")
            value = words[index + 1]
            if word == "--verify":
                verify = value
            elif word == "--scope":
                scope.append(value)
            else:
                if not value.isdigit() or int(value) < 1:
                    raise GoalUsageError("--attempts must be a positive number")
                attempts = int(value)
            index += 2
            continue
        objective.append(word)
        index += 1
    if not objective:
        raise GoalUsageError("name the objective")
    if not verify:
        raise GoalUsageError("a goal needs --verify <command>: the command that decides whether it is reached")
    return GoalRequest("set", " ".join(objective), verify, attempts, tuple(scope))


async def handle_goal(app: NerdvanaApp, args: str) -> None:
    """Handle ``/goal``."""
    loop = app._agent_loop
    if loop is None:
        app._add_chat_message("[red]The agent is not ready yet.[/red]")
        return
    try:
        request = parse_goal_args(args)
    except GoalUsageError as exc:
        app._add_chat_message(f"[red]{escape(str(exc))}[/red]\n[dim]{escape(USAGE)}[/dim]")
        return

    goal = loop.goal
    if request.action == "show":
        app._add_chat_message(f"[dim]{escape(goal.describe()) if goal else 'No goal. ' + escape(USAGE)}[/dim]")
        return
    if request.action == "set":
        try:
            new = Goal(request.objective, request.verify, max_attempts=request.attempts or app.settings.goal.max_attempts, scope=list(request.scope))
        except GoalError as exc:
            app._add_chat_message(f"[red]{escape(str(exc))}[/red]")
            return
        loop.set_goal(new)
        app._start_prompt(f"/goal {request.objective}", f"{request.objective}\n\n(The task is done when `{request.verify}` exits with status 0; it is run when you say you are finished.)")
        return
    if goal is None:
        app._add_chat_message("[dim]No goal to change.[/dim]")
        return
    if request.action == "clear":
        loop.set_goal(None)
        app._add_chat_message("[dim]Goal cleared.[/dim]")
        return
    goal.status = PAUSED if request.action == "pause" else ACTIVE
    if request.action == "resume":
        goal.attempts = 0
    loop.set_goal(goal)
    app._add_chat_message(f"[dim]Goal {'paused' if request.action == 'pause' else 'resumed'}: {escape(goal.describe())}[/dim]")
