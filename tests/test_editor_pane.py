"""Tests for the direct editor pane."""

from __future__ import annotations

from pathlib import Path

from textual.widgets import TextArea

from nerdvana_cli.ui.editor_pane import EditorPane, language_for_path


def test_editor_pane_hidden_by_default(tmp_path: Path) -> None:
    pane = EditorPane(project_root=tmp_path)
    assert "hidden" in pane.classes


def test_editor_pane_wraps_text_area(tmp_path: Path) -> None:
    pane = EditorPane(project_root=tmp_path)
    assert isinstance(pane.text_area, TextArea)


def test_editor_pane_loads_buffer(tmp_path: Path) -> None:
    pane = EditorPane(project_root=tmp_path)
    pane.load_buffer(relative_path="sample.py", text="print('hi')\n", language="python")

    assert pane.current_path() == "sample.py"
    assert pane.current_text() == "print('hi')\n"
    assert pane.is_dirty() is False


def test_editor_pane_tracks_dirty_state(tmp_path: Path) -> None:
    pane = EditorPane(project_root=tmp_path)
    pane.load_buffer(relative_path="sample.py", text="value = 1\n")
    pane.set_current_text("value = 2\n")

    assert pane.is_dirty() is True
    pane.mark_clean()
    assert pane.is_dirty() is False


def test_language_for_path_maps_common_extensions() -> None:
    assert language_for_path("a.py") == "python"
    assert language_for_path("a.md") == "markdown"
    assert language_for_path("a.unknown") is None
