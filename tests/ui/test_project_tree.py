"""Tests for the project tree pane."""

from __future__ import annotations

from pathlib import Path

from nerdvana_cli.ui.project_tree import ProjectTreePane


def test_project_tree_keeps_root(tmp_path: Path) -> None:
    pane = ProjectTreePane(root_path=tmp_path)
    assert pane.root_path == str(tmp_path.resolve())


def test_project_tree_hidden_by_default(tmp_path: Path) -> None:
    pane = ProjectTreePane(root_path=tmp_path)
    assert "hidden" in pane.classes


def test_project_tree_toggle_returns_visible_state(tmp_path: Path) -> None:
    pane = ProjectTreePane(root_path=tmp_path)
    became_visible = pane.toggle_pane()
    assert became_visible is True
    assert "hidden" not in pane.classes


def test_project_tree_tracks_selected_relative_path(tmp_path: Path) -> None:
    target = tmp_path / "nerdvana_cli" / "ui" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('x')\n", encoding="utf-8")

    pane = ProjectTreePane(root_path=tmp_path)
    result = pane.set_selected_path(target)

    assert result == "nerdvana_cli/ui/app.py"
    assert pane.selected_relative_path() == "nerdvana_cli/ui/app.py"


def test_project_tree_creates_tree_lazily(tmp_path: Path) -> None:
    pane = ProjectTreePane(root_path=tmp_path)
    assert pane._tree is None
