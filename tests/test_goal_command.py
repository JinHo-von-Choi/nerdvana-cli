"""/goal: parsing what the user typed and acting on the loop's goal.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from typing import Any

import pytest

from nerdvana_cli.commands.goal_command import GoalRequest, GoalUsageError, handle_goal, parse_goal_args
from nerdvana_cli.core.goal import ACTIVE, PAUSED, Goal
from nerdvana_cli.core.settings import NerdvanaSettings

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_no_arguments_shows_and_the_keywords_are_actions() -> None:
    assert parse_goal_args("") == GoalRequest("show")
    assert parse_goal_args("  pause ") == GoalRequest("pause")
    assert parse_goal_args("resume") == GoalRequest("resume")
    assert parse_goal_args("clear") == GoalRequest("clear")


def test_an_objective_with_a_quoted_command_and_attempts() -> None:
    request = parse_goal_args('make the parser handle quotes --verify "pytest -q tests/test_parser.py" --attempts 3')
    assert request == GoalRequest("set", "make the parser handle quotes", "pytest -q tests/test_parser.py", 3)


def test_the_flags_may_come_first() -> None:
    assert parse_goal_args("--verify true fix it").objective == "fix it"


@pytest.mark.parametrize("text", [
    "fix the bug",                      # no verification command
    "--verify true",                    # no objective
    "fix it --verify",                  # flag without a value
    "fix it --verify true --attempts x",
    "fix it --verify true --attempts 0",
    'fix "unclosed --verify true',
])
def test_unusable_arguments_are_refused_with_a_reason(text: str) -> None:
    with pytest.raises(GoalUsageError):
        parse_goal_args(text)


# ---------------------------------------------------------------------------
# The handler
# ---------------------------------------------------------------------------


class _Loop:
    def __init__(self, goal: Goal | None = None) -> None:
        self.goal = goal

    def set_goal(self, goal: Goal | None) -> None:
        self.goal = goal


class _App:
    def __init__(self, loop: _Loop | None) -> None:
        self._agent_loop = loop
        self.settings    = NerdvanaSettings()
        self.messages: list[str] = []
        self.started:  list[tuple[str, str]] = []

    def _add_chat_message(self, markup: str, **_: Any) -> None:
        self.messages.append(markup)

    def _start_prompt(self, shown: str, prompt: str) -> None:
        self.started.append((shown, prompt))


async def test_setting_a_goal_saves_it_and_starts_the_work() -> None:
    loop = _Loop()
    app  = _App(loop)
    await handle_goal(app, "fix it --verify 'make test'")  # type: ignore[arg-type]
    assert loop.goal is not None and (loop.goal.objective, loop.goal.verify, loop.goal.max_attempts) == ("fix it", "make test", 5)
    assert app.started and "make test" in app.started[0][1]


async def test_a_bad_command_line_changes_nothing_and_explains() -> None:
    loop = _Loop()
    app  = _App(loop)
    await handle_goal(app, "fix it")  # type: ignore[arg-type]
    assert loop.goal is None and app.started == []
    assert "--verify" in app.messages[0]


async def test_show_pause_resume_and_clear() -> None:
    goal = Goal(objective="x", verify="true", attempts=3)
    loop = _Loop(goal)
    app  = _App(loop)
    await handle_goal(app, "")  # type: ignore[arg-type]
    assert "x" in app.messages[-1] and "verify: true" in app.messages[-1]
    await handle_goal(app, "pause")  # type: ignore[arg-type]
    assert loop.goal is not None and loop.goal.status == PAUSED
    await handle_goal(app, "resume")  # type: ignore[arg-type]
    assert loop.goal.status == ACTIVE and loop.goal.attempts == 0
    await handle_goal(app, "clear")  # type: ignore[arg-type]
    assert loop.goal is None
    await handle_goal(app, "pause")  # type: ignore[arg-type]
    assert "No goal to change" in app.messages[-1]


async def test_without_an_agent_loop_the_command_says_so() -> None:
    app = _App(None)
    await handle_goal(app, "")  # type: ignore[arg-type]
    assert "not ready" in app.messages[0]


def test_the_command_is_registered_and_listed() -> None:
    from nerdvana_cli.ui.command_dispatcher import _build_handler_map
    from nerdvana_cli.ui.widgets.command_menu import SLASH_COMMANDS

    assert "/goal" in _build_handler_map()
    assert "/goal" in {name for name, _ in SLASH_COMMANDS}


def test_scope_flags_are_collected_and_reach_the_goal() -> None:
    request = parse_goal_args("fix it --verify true --scope tests --scope docs")
    assert request.scope == ("tests", "docs")
    assert parse_goal_args("fix it --verify true").scope == ()
    with pytest.raises(GoalUsageError):
        parse_goal_args("fix it --verify true --scope")


async def test_the_goal_set_by_the_command_keeps_the_scope() -> None:
    loop = _Loop()
    await handle_goal(_App(loop), "fix it --verify true --scope tests")  # type: ignore[arg-type]
    assert loop.goal is not None and loop.goal.scope == ["tests"]
