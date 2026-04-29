"""Direct editor pane for project files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static, TextArea


class EditorPane(Vertical):
    """Single-buffer text editor pane backed by Textual's TextArea."""

    DEFAULT_CSS = """
    EditorPane {
        width: 1fr;
        min-width: 44;
        height: 1fr;
        border-left: solid $accent;
        background: $surface;
    }
    EditorPane.hidden {
        display: none;
    }
    #editor-header {
        height: 1;
        padding: 0 1;
        background: $primary-background;
        color: $text;
        text-style: bold;
    }
    #editor-text {
        height: 1fr;
        border: none;
    }
    """

    def __init__(self, project_root: str | Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._project_root  = str(Path(project_root).expanduser().resolve())
        self._relative_path = ""
        self._loaded_text   = ""
        self._dirty         = False
        self._header        = Static(self._header_text(), id="editor-header")
        self._text_area     = TextArea(
            "",
            soft_wrap             = False,
            tab_behavior          = "indent",
            show_line_numbers     = True,
            highlight_cursor_line = True,
            id                    = "editor-text",
        )
        self.add_class("hidden")

    @property
    def text_area(self) -> TextArea:
        return self._text_area

    def compose(self) -> ComposeResult:
        yield self._header
        yield self._text_area

    def show_pane(self) -> None:
        self.remove_class("hidden")

    def hide_pane(self) -> None:
        self.add_class("hidden")

    def toggle_pane(self) -> bool:
        is_hidden = "hidden" in self.classes
        self.set_class(not is_hidden, "hidden")
        return is_hidden

    def focus_editor(self) -> None:
        self._text_area.focus()

    def load_buffer(
        self,
        relative_path: str,
        text:          str,
        language:      str | None = None,
    ) -> None:
        self._relative_path = relative_path
        self._loaded_text   = text
        self._dirty         = False

        self._text_area.language = language
        self._text_area.load_text(text)
        self._header.update(self._header_text())

    def set_current_text(self, text: str) -> None:
        self._text_area.load_text(text)
        self.refresh_dirty_state()

    def current_text(self) -> str:
        return self._text_area.text

    def current_path(self) -> str:
        return self._relative_path

    def is_dirty(self) -> bool:
        self.refresh_dirty_state()
        return self._dirty

    def mark_clean(self) -> None:
        self._loaded_text = self.current_text()
        self._dirty       = False
        self._header.update(self._header_text())

    def refresh_dirty_state(self) -> None:
        self._dirty = self.current_text() != self._loaded_text
        self._header.update(self._header_text())

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self.refresh_dirty_state()

    def _header_text(self) -> str:
        path = self._relative_path or "(no file)"
        mark = " *" if self._dirty else ""
        return f"EDIT {path}{mark}"


def language_for_path(path: str) -> str | None:
    """Return a Textual language name for common project file extensions."""
    suffix = Path(path).suffix.lower()
    return {
        ".css":  "css",
        ".html": "html",
        ".js":   "javascript",
        ".json": "json",
        ".md":   "markdown",
        ".py":   "python",
        ".toml": "toml",
        ".ts":   "typescript",
        ".yaml": "yaml",
        ".yml":  "yaml",
    }.get(suffix)
