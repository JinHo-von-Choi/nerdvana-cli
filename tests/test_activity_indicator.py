"""Tests for ActivityIndicator widget (render + reactive watch)."""

from __future__ import annotations

import time

from nerdvana_cli.core.activity_state import ActivityState
from nerdvana_cli.ui.app import ActivityIndicator


class TestActivityIndicatorLabel:
    """_refresh_label covers render output without a Textual app context."""

    def _label(self, state: ActivityState) -> str:
        """Instantiate widget and call _refresh_label; capture update arg."""
        widget   = ActivityIndicator()
        captured: list[str] = []
        widget.update = lambda s: captured.append(s)  # type: ignore[method-assign]
        widget._refresh_label(state)
        return captured[-1] if captured else ""

    def test_idle_icon(self) -> None:
        label = self._label(ActivityState(phase="idle", label="Ready"))
        assert label.startswith("●")

    def test_thinking_icon(self) -> None:
        label = self._label(ActivityState(phase="thinking", label="Thinking"))
        assert label.startswith("◐")

    def test_waiting_api_icon(self) -> None:
        label = self._label(ActivityState(phase="waiting_api", label="Waiting"))
        assert label.startswith("◔")

    def test_streaming_icon(self) -> None:
        label = self._label(ActivityState(phase="streaming", label="Streaming"))
        assert label.startswith("◑")

    def test_tool_running_icon(self) -> None:
        label = self._label(ActivityState(phase="tool_running", label="Tool"))
        assert label.startswith("◉")

    def test_detail_appended(self) -> None:
        label = self._label(ActivityState(phase="idle", label="Ready", detail="Bash"))
        assert ": Bash" in label

    def test_no_detail_no_colon(self) -> None:
        label = self._label(ActivityState(phase="idle", label="Ready", detail=""))
        assert ": " not in label

    def test_elapsed_shown_when_one_or_more_seconds(self) -> None:
        state = ActivityState(phase="thinking", label="Thinking", started_at=time.time() - 5)
        label = self._label(state)
        assert "[5s]" in label or "[4s]" in label or "[6s]" in label

    def test_elapsed_hidden_when_under_one_second(self) -> None:
        state = ActivityState(phase="thinking", label="Thinking", started_at=time.time())
        label = self._label(state)
        assert "[0s]" not in label

    def test_elapsed_absent_when_no_started_at(self) -> None:
        state = ActivityState(phase="thinking", label="Thinking", started_at=None)
        label = self._label(state)
        assert "[" not in label or "s]" not in label

    def test_unknown_phase_falls_back_to_bullet(self) -> None:
        state = ActivityState(phase="unknown_phase", label="X")  # type: ignore[arg-type]
        label = self._label(state)
        assert label.startswith("●")


class TestActivityIndicatorWatchState:
    """watch_state updates CSS classes correctly."""

    def _watched_widget(self, state: ActivityState) -> ActivityIndicator:
        widget = ActivityIndicator()
        # Stub add_class / remove_class to track applied classes
        widget._applied: set[str] = set()  # type: ignore[attr-defined]

        def _add(cls: str) -> None:
            widget._applied.add(cls)

        def _remove(cls: str) -> None:
            widget._applied.discard(cls)

        widget.add_class    = _add     # type: ignore[method-assign]
        widget.remove_class = _remove  # type: ignore[method-assign]
        widget.update       = lambda _: None  # type: ignore[method-assign]
        widget.watch_state(state)
        return widget

    def test_thinking_adds_thinking_class(self) -> None:
        w = self._watched_widget(ActivityState(phase="thinking", label="T"))
        assert "-thinking" in w._applied  # type: ignore[attr-defined]

    def test_tool_running_adds_tool_class(self) -> None:
        w = self._watched_widget(ActivityState(phase="tool_running", label="T"))
        assert "-tool" in w._applied  # type: ignore[attr-defined]

    def test_streaming_adds_stream_class(self) -> None:
        w = self._watched_widget(ActivityState(phase="streaming", label="S"))
        assert "-stream" in w._applied  # type: ignore[attr-defined]

    def test_idle_adds_idle_class(self) -> None:
        w = self._watched_widget(ActivityState(phase="idle", label="Ready"))
        assert "-idle" in w._applied  # type: ignore[attr-defined]

    def test_waiting_api_adds_waiting_class(self) -> None:
        w = self._watched_widget(ActivityState(phase="waiting_api", label="Wait"))
        assert "-waiting" in w._applied  # type: ignore[attr-defined]

    def test_previous_classes_removed(self) -> None:
        widget = ActivityIndicator()
        removed: set[str] = set()

        widget.add_class    = lambda c: None  # type: ignore[method-assign]
        widget.remove_class = lambda c: removed.add(c)  # type: ignore[method-assign]
        widget.update       = lambda _: None  # type: ignore[method-assign]

        widget.watch_state(ActivityState(phase="idle", label="Ready"))
        # All class variants must have been cleared before the new one is added
        all_classes = {"-idle", "-thinking", "-waiting", "-stream", "-tool"}
        assert all_classes.issubset(removed)
