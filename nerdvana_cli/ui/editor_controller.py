"""Editor buffer controller of the Textual TUI.

Extracted from ``NerdvanaApp`` so the App class stays focused on widget composition.
The controller opens, saves and path-checks the files shown in the editor pane; every
file access goes through the validated, symlink-aware path helpers. It takes the App
reference and reads the project root from it on each call.

작성자: 최진호
작성일: 2026-10-03
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from nerdvana_cli.ui.editor_pane import EditorPane, language_for_path
from nerdvana_cli.utils.path import safe_open_fd, validate_path

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp

MAX_EDITOR_FILE_BYTES = 1_000_000


class EditorBufferController:
    """File I/O and pane wiring of the direct editor."""

    def __init__(self, app: NerdvanaApp) -> None:
        self._app = app

    def read(self, relative_path: str) -> str:
        """Read a project file through validated, symlink-aware path handling."""
        root       = self._app._project_root
        path_error = validate_path(relative_path, root)
        if path_error:
            raise PermissionError(path_error)

        fd = safe_open_fd(relative_path, root, os.O_RDONLY)
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(MAX_EDITOR_FILE_BYTES + 1)

        if len(data) > MAX_EDITOR_FILE_BYTES:
            raise ValueError(f"Editor refuses files larger than {MAX_EDITOR_FILE_BYTES} bytes")
        if b"\x00" in data:
            raise ValueError("Editor refuses binary files")
        return data.decode("utf-8", errors="replace")

    def write(self, relative_path: str, content: str) -> None:
        """Write a project file through validated, symlink-aware path handling."""
        root       = self._app._project_root
        path_error = validate_path(relative_path, root)
        if path_error:
            raise PermissionError(path_error)

        fd = safe_open_fd(relative_path, root, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)

    def relative_path(self, path: str | Path) -> str:
        """Convert an absolute selected path into a validated project-relative path."""
        root     = Path(self._app._project_root).resolve()
        selected = Path(path).expanduser().resolve()
        try:
            relative_path = str(selected.relative_to(root))
        except ValueError as exc:
            raise PermissionError(f"Path is outside project root: {selected}") from exc

        path_error = validate_path(relative_path, self._app._project_root)
        if path_error:
            raise PermissionError(path_error)
        return relative_path

    def open(self, relative_path: str) -> None:
        """Load a relative project path into the direct editor pane."""
        text   = self.read(relative_path)
        editor = self._app.query_one("#editor-pane", EditorPane)
        editor.load_buffer(
            relative_path = relative_path,
            text          = text,
            language      = language_for_path(relative_path),
        )
        editor.show_pane()
        editor.focus_editor()

    def save_active(self) -> None:
        """Save the active editor buffer and report the outcome in the chat."""
        editor        = self._app.query_one("#editor-pane", EditorPane)
        relative_path = editor.current_path()
        if not relative_path:
            self._app._add_chat_message("[dim]No editor buffer to save[/dim]", raw_text="No editor buffer to save")
            return

        try:
            self.write(relative_path, editor.current_text())
        except Exception as exc:
            self._app._add_chat_message(f"[red]Save failed: {exc}[/red]", raw_text=f"Save failed: {exc}")
            return

        editor.mark_clean()
        self._app._add_chat_message(f"[dim]Saved {relative_path}[/dim]", raw_text=f"Saved {relative_path}")

    def open_selected(self, path: str | Path) -> None:
        """Open a file picked in the project tree, reporting a failure in the chat."""
        try:
            self.open(self.relative_path(path))
        except Exception as exc:
            self._app._add_chat_message(f"[red]Open failed: {exc}[/red]", raw_text=f"Open failed: {exc}")
