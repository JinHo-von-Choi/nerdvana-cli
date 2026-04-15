"""Per-turn context reminder and tool result ring buffer."""
from __future__ import annotations

from nerdvana_cli.core.context_reminder import (
    ContextReminder,
    RecentToolResult,
)


def test_reminder_includes_turn_number_and_cwd(tmp_path) -> None:
    reminder = ContextReminder(cwd=str(tmp_path), max_recent=5)
    text = reminder.build(turn=3)
    assert "turn=3" in text
    assert str(tmp_path) in text


def test_recent_tool_buffer_is_bounded() -> None:
    reminder = ContextReminder(cwd=".", max_recent=3)
    for i in range(10):
        reminder.record_tool(
            RecentToolResult(
                name=f"Tool{i}",
                args_summary=f"arg-{i}",
                preview=f"result {i}",
                ok=True,
            )
        )
    text = reminder.build(turn=1)
    # Only the 3 most recent survive
    for i in (7, 8, 9):
        assert f"Tool{i}" in text
    assert "Tool6" not in text


def test_build_is_empty_when_nothing_changed() -> None:
    reminder = ContextReminder(cwd=".", max_recent=5)
    # No tools recorded — reminder still returns a non-empty base block
    text = reminder.build(turn=1)
    assert "turn=1" in text


def test_render_uses_system_reminder_tag() -> None:
    reminder = ContextReminder(cwd=".", max_recent=5)
    text = reminder.build(turn=1)
    assert text.startswith("<system-reminder>")
    assert text.endswith("</system-reminder>")
