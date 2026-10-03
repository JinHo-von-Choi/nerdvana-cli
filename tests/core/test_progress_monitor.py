"""A run that is busy but going nowhere is noticed once, counted, and told so.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from typing import Any

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.hooks import HookEngine
from nerdvana_cli.core.progress_monitor import EDIT, OTHER, READ, ProgressMonitor
from nerdvana_cli.core.signals import NO_PROGRESS
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolRegistry
from nerdvana_cli.core.tool_executor import ToolExecutor
from nerdvana_cli.types import ToolResult

# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


def _failed_edits(monitor: ProgressMonitor, path: str, count: int) -> list[str]:
    """Run *count* turns that each hold one failed edit of *path*; the note each turn ended with."""
    notes = []
    for _ in range(count):
        monitor.observe(EDIT, path, True)
        notes.append(monitor.end_turn())
    return notes


def test_failed_edits_of_one_file_are_noted_when_the_limit_is_reached() -> None:
    notes = _failed_edits(ProgressMonitor(failed_edit_limit=3), "a.py", 3)
    assert notes[:2] == ["", ""]
    assert "last 3 edits to a.py all failed" in notes[2]


def test_the_edit_note_comes_once_per_stall() -> None:
    monitor = ProgressMonitor(failed_edit_limit=2)
    notes   = _failed_edits(monitor, "a.py", 6)
    assert [bool(note) for note in notes] == [False, True, False, False, False, False]


def test_an_applied_edit_ends_the_stall_so_a_new_one_can_be_noted() -> None:
    monitor = ProgressMonitor(failed_edit_limit=2)
    assert any(_failed_edits(monitor, "a.py", 2))
    monitor.observe(EDIT, "a.py", False)
    assert monitor.end_turn() == ""
    assert any(_failed_edits(monitor, "a.py", 2))


def test_failed_edits_of_different_files_do_not_add_up() -> None:
    monitor = ProgressMonitor(failed_edit_limit=3)
    notes   = [*_failed_edits(monitor, "a.py", 2), *_failed_edits(monitor, "b.py", 2), *_failed_edits(monitor, "a.py", 2)]
    assert not any(notes)


def test_a_read_between_failed_edits_does_not_reset_the_streak() -> None:
    monitor = ProgressMonitor(failed_edit_limit=3)
    notes   = []
    for _ in range(3):
        monitor.observe(READ, "", False)
        monitor.observe(EDIT, "a.py", True)
        notes.append(monitor.end_turn())
    assert [bool(note) for note in notes] == [False, False, True]


def test_turns_that_only_read_are_noted_when_the_limit_is_reached() -> None:
    monitor = ProgressMonitor(read_turn_limit=4)
    notes   = []
    for _ in range(5):
        monitor.observe(READ, "", False)
        monitor.observe(READ, "", False)
        notes.append(monitor.end_turn())
    assert [bool(note) for note in notes] == [False, False, False, True, False]
    assert "last 4 turns only read" in notes[3]


def test_a_turn_with_a_command_or_an_edit_is_progress_and_resets_the_count() -> None:
    monitor = ProgressMonitor(read_turn_limit=3)
    notes   = []
    for kind in (READ, READ, OTHER, READ, READ, EDIT, READ, READ):
        monitor.observe(kind, "a.py" if kind == EDIT else "", False)
        notes.append(monitor.end_turn())
    assert not any(notes)


def test_a_turn_with_a_read_and_a_command_is_not_a_read_only_turn() -> None:
    monitor = ProgressMonitor(read_turn_limit=2)
    for _ in range(4):
        monitor.observe(READ, "", False)
        monitor.observe(OTHER, "", False)
        assert monitor.end_turn() == ""


def test_the_read_note_can_come_again_after_progress() -> None:
    monitor = ProgressMonitor(read_turn_limit=2)
    stalls  = 0
    for kind in (READ, READ, READ, OTHER, READ, READ, READ):
        monitor.observe(kind, "", False)
        stalls += bool(monitor.end_turn())
    assert stalls == 2


def test_a_limit_of_zero_turns_the_check_off() -> None:
    monitor = ProgressMonitor(failed_edit_limit=0, read_turn_limit=0)
    assert not any(_failed_edits(monitor, "a.py", 50))
    for _ in range(50):
        monitor.observe(READ, "", False)
        assert monitor.end_turn() == ""


# ---------------------------------------------------------------------------
# Wired into the tool executor
# ---------------------------------------------------------------------------


class _Edit(BaseTool[Any]):
    name             = "FileEdit"
    description_text = "an edit that is refused"
    input_schema: dict[str, Any] = {"type": "object", "properties": {"path": {"type": "string"}, "n": {"type": "integer"}}}

    async def call(self, args: Any, context: ToolContext, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="The anchor no longer matches.", is_error=True)


class _Peek(BaseTool[Any]):
    name             = "Peek"
    description_text = "reads something"
    category         = ToolCategory.READ
    input_schema: dict[str, Any] = {"type": "object", "properties": {"n": {"type": "integer"}}}

    async def call(self, args: Any, context: ToolContext, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="contents")


def _executor(failed_edits: int = 3, read_turns: int = 12) -> ToolExecutor:
    settings = NerdvanaSettings()
    settings.session.no_progress_failed_edits = failed_edits
    settings.session.no_progress_read_turns   = read_turns
    registry = ToolRegistry()
    registry.register(_Edit())
    registry.register(_Peek())
    return ToolExecutor(registry=registry, hooks=HookEngine(), settings=settings)


async def test_a_repeated_failing_edit_of_one_file_adds_one_note_and_one_signal() -> None:
    executor = _executor(failed_edits=3)
    context  = ToolContext(cwd=".")
    results  = []
    for n in range(5):
        # The arguments differ each time, so the identical-call guard stays out of it.
        results.extend(await executor.run_batch([{"id": f"e{n}", "name": "FileEdit", "input": {"path": "a.py", "n": n}}], context))
    noted = [result for result in results if "[Note: the last 3 edits to a.py all failed" in result.content]
    assert len(noted) == 1 and noted[0].tool_use_id == "e2"
    assert executor.signals[NO_PROGRESS] == 1


async def test_turns_that_only_read_add_one_note_and_one_signal() -> None:
    executor = _executor(read_turns=3)
    context  = ToolContext(cwd=".")
    results  = []
    for n in range(6):
        results.extend(await executor.run_batch([{"id": f"p{n}", "name": "Peek", "input": {"n": n}}], context))
    noted = [result for result in results if "only read or searched" in result.content]
    assert len(noted) == 1 and noted[0].tool_use_id == "p2"
    assert executor.signals[NO_PROGRESS] == 1


async def test_with_both_limits_off_results_and_signals_are_unchanged() -> None:
    executor = _executor(failed_edits=0, read_turns=0)
    context  = ToolContext(cwd=".")
    results  = []
    for n in range(20):
        results.extend(await executor.run_batch([{"id": f"e{n}", "name": "FileEdit", "input": {"path": "a.py", "n": n}}], context))
        results.extend(await executor.run_batch([{"id": f"p{n}", "name": "Peek", "input": {"n": n}}], context))
    assert all("[Note:" not in result.content for result in results)
    assert NO_PROGRESS not in executor.signals


def test_the_no_progress_signal_is_not_in_the_default_escalation_signals() -> None:
    assert NO_PROGRESS not in NerdvanaSettings().session.escalation_signals
