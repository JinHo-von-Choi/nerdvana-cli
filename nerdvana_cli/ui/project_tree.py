"""Project file tree pane for the Textual REPL."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DirectoryTree, Static


class ProjectTreePane(Vertical):
    """Toggleable project tree backed by Textual's DirectoryTree."""

    DEFAULT_CSS = """
    ProjectTreePane {
        width: 34;
        min-width: 24;
        height: 1fr;
        border-right: solid $accent;
        background: $surface;
    }
    ProjectTreePane.hidden {
        display: none;
    }
    #project-tree-header {
        height: 1;
        padding: 0 1;
        background: $primary-background;
        color: $text;
        text-style: bold;
    }
    #project-tree {
        height: 1fr;
        padding: 0 1;
    }
    """

    def __init__(self, root_path: str | Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.root_path      = str(Path(root_path).expanduser().resolve())
        self._selected_path = ""
        self._header        = Static(self._header_text(), id="project-tree-header")
        self._tree: DirectoryTree | None
        self._tree          = None
        self.add_class("hidden")

    def compose(self) -> ComposeResult:
        self._tree = DirectoryTree(self.root_path, id="project-tree")
        yield self._header
        yield self._tree

    def show_pane(self) -> None:
        self.remove_class("hidden")

    def hide_pane(self) -> None:
        self.add_class("hidden")

    def toggle_pane(self) -> bool:
        is_hidden = "hidden" in self.classes
        self.set_class(not is_hidden, "hidden")
        return is_hidden

    def focus_tree(self) -> None:
        if self._tree is not None:
            self._tree.focus()

    def selected_relative_path(self) -> str | None:
        if not self._selected_path:
            return None
        return self._selected_path

    def set_selected_path(self, path: str | Path) -> str:
        selected            = Path(path).expanduser().resolve()
        root                = Path(self.root_path).resolve()
        self._selected_path = str(selected.relative_to(root))
        self._header.update(self._header_text())
        return self._selected_path

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.set_selected_path(event.path)

    def _header_text(self) -> str:
        if self._selected_path:
            return f"FILES {self._selected_path}"
        return f"FILES {Path(self.root_path).name}"
