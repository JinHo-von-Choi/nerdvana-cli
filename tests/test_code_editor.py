"""Unit tests for nerdvana_cli.core.code_editor.

Tests PreviewEntry, CodeEditor preview creation, apply, STALE detection,
and LRU eviction. No actual LSP process required — workspace edits are
applied directly to temporary files.

작성자: 최진호
작성일: 2026-04-18
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import pytest

from nerdvana_cli.core.code_editor import (
    CodeEditor,
    PreviewEntry,
    StalePreviewError,
    UnknownPreviewError,
    _sha256,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_editor(tmp_path: Path, lru_max: int = 20) -> CodeEditor:
    return CodeEditor(project_root=str(tmp_path), lru_max=lru_max)


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Test: preview creation
# ---------------------------------------------------------------------------


class TestCodeEditorPreview:
    def test_create_preview_returns_id_and_diff(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "foo.py"
        _write(target, "def foo(): pass\n")

        workspace_edit: dict = {
            "documentChanges": [
                {
                    "textDocument": {"uri": target.as_uri(), "version": None},
                    "edits": [
                        {
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end":   {"line": 1, "character": 0},
                            },
                            "newText": "def foo(): return 42\n",
                        }
                    ],
                }
            ]
        }
        new_contents = {str(target): "def foo(): return 42\n"}

        pid, diff = editor.create_preview("replace_body", workspace_edit, new_contents)

        assert len(pid) == 12   # 6 bytes hex = 12 chars
        assert "foo" in diff
        assert editor.pending_count() == 1

    def test_preview_entry_stored(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "bar.py"
        _write(target, "x = 1\n")

        pid, _ = editor.create_preview(
            "replace_body",
            {},
            {str(target): "x = 2\n"},
        )
        entry = editor.get(pid)
        assert entry is not None
        assert isinstance(entry, PreviewEntry)
        assert entry.kind == "replace_body"
        assert str(target) in entry.target_files

    def test_no_change_diff_text(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "same.py"
        content = "a = 1\n"
        _write(target, content)

        _, diff = editor.create_preview("replace_body", {}, {str(target): content})
        assert diff == "(no changes)"


# ---------------------------------------------------------------------------
# Test: apply
# ---------------------------------------------------------------------------


class TestCodeEditorApply:
    def test_apply_writes_file(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "apply_test.py"
        _write(target, "def hello(): pass\n")

        workspace_edit = {
            "documentChanges": [
                {
                    "textDocument": {"uri": target.as_uri(), "version": None},
                    "edits": [
                        {
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end":   {"line": 1, "character": 0},
                            },
                            "newText": "def hello(): return 'hi'\n",
                        }
                    ],
                }
            ]
        }
        pid, _ = editor.create_preview(
            "replace_body",
            workspace_edit,
            {str(target): "def hello(): return 'hi'\n"},
        )

        result = editor.apply(pid)
        assert result["status"] == "applied"
        assert target.read_text(encoding="utf-8") == "def hello(): return 'hi'\n"
        # consumed — not in store anymore
        assert editor.get(pid) is None

    def test_apply_unknown_id_raises(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        with pytest.raises(UnknownPreviewError):
            editor.apply("deadbeefcafe")

    def test_stale_detection(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "stale.py"
        _write(target, "x = 1\n")

        pid, _ = editor.create_preview(
            "replace_body",
            {},
            {str(target): "x = 2\n"},
        )
        # Mutate the file after preview creation
        _write(target, "x = 99\n")

        with pytest.raises(StalePreviewError) as exc_info:
            editor.apply(pid)

        assert str(target) in exc_info.value.changed_paths
        assert exc_info.value.preview_id == pid


# ---------------------------------------------------------------------------
# Test: LRU eviction
# ---------------------------------------------------------------------------


class TestCodeEditorLRU:
    def test_lru_evicts_oldest(self, tmp_path: Path) -> None:
        editor    = _make_editor(tmp_path, lru_max=3)
        target    = tmp_path / "f.py"
        _write(target, "a\n")

        ids: list[str] = []
        for i in range(4):
            pid, _ = editor.create_preview(
                "replace_body", {}, {str(target): f"{i}\n"}
            )
            ids.append(pid)

        # Only 3 remain; the oldest (ids[0]) was evicted
        assert editor.pending_count() == 3
        assert editor.get(ids[0]) is None
        assert editor.get(ids[1]) is not None
        assert editor.get(ids[3]) is not None

    def test_lru_max_20_default(self, tmp_path: Path) -> None:
        editor = CodeEditor(project_root=str(tmp_path))
        assert editor._lru_max == 20

    def test_discard_removes_entry(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "d.py"
        _write(target, "z\n")
        pid, _ = editor.create_preview("replace_body", {}, {str(target): "w\n"})
        assert editor.discard(pid) is True
        assert editor.get(pid) is None

    def test_discard_nonexistent(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        assert editor.discard("nonexistent") is False


# ---------------------------------------------------------------------------
# Test: prepare_insert_before
# ---------------------------------------------------------------------------


class TestCodeEditorInsertBefore:
    def test_insert_before_preview_returned(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "ib.py"
        _write(target, "def foo():\n    pass\n")
        original_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)

        pid, diff = editor.prepare_insert_before(
            name_path      = "foo",
            relative_path  = "ib.py",
            body           = "# inserted before foo\n",
            abs_path       = str(target),
            start_line     = 0,
            original_lines = original_lines,
        )

        assert len(pid) == 12
        assert "inserted before foo" in diff
        assert editor.pending_count() == 1

    def test_insert_before_apply_writes_file(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "ib_apply.py"
        _write(target, "def bar():\n    pass\n")
        original_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)

        pid, _ = editor.prepare_insert_before(
            name_path      = "bar",
            relative_path  = "ib_apply.py",
            body           = "X = 1\n",
            abs_path       = str(target),
            start_line     = 0,
            original_lines = original_lines,
        )
        result = editor.apply(pid)
        assert result["status"] == "applied"
        content = target.read_text(encoding="utf-8")
        assert content.startswith("X = 1\n")
        assert "def bar" in content

    def test_insert_before_kind_stored(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "ib_kind.py"
        _write(target, "pass\n")
        original_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)

        pid, _ = editor.prepare_insert_before(
            name_path      = "x",
            relative_path  = "ib_kind.py",
            body           = "# before\n",
            abs_path       = str(target),
            start_line     = 0,
            original_lines = original_lines,
        )
        entry = editor.get(pid)
        assert entry is not None
        assert entry.kind == "insert_before"


# ---------------------------------------------------------------------------
# Test: prepare_insert_after
# ---------------------------------------------------------------------------


class TestCodeEditorInsertAfter:
    def test_insert_after_preview_returned(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "ia.py"
        _write(target, "def foo():\n    pass\n\ndef bar():\n    pass\n")
        original_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)

        pid, diff = editor.prepare_insert_after(
            name_path      = "foo",
            relative_path  = "ia.py",
            body           = "# inserted after foo\n",
            abs_path       = str(target),
            end_line       = 2,   # line index of blank line after def foo block
            original_lines = original_lines,
        )

        assert len(pid) == 12
        assert "inserted after foo" in diff
        assert editor.pending_count() == 1

    def test_insert_after_apply_writes_file(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "ia_apply.py"
        _write(target, "def foo():\n    pass\n")
        original_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)

        pid, _ = editor.prepare_insert_after(
            name_path      = "foo",
            relative_path  = "ia_apply.py",
            body           = "def bar(): pass\n",
            abs_path       = str(target),
            end_line       = 2,   # end of file
            original_lines = original_lines,
        )
        result = editor.apply(pid)
        assert result["status"] == "applied"
        content = target.read_text(encoding="utf-8")
        assert "def foo" in content
        assert "def bar" in content

    def test_insert_after_kind_stored(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "ia_kind.py"
        _write(target, "pass\n")
        original_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)

        pid, _ = editor.prepare_insert_after(
            name_path      = "x",
            relative_path  = "ia_kind.py",
            body           = "# after\n",
            abs_path       = str(target),
            end_line       = 1,
            original_lines = original_lines,
        )
        entry = editor.get(pid)
        assert entry is not None
        assert entry.kind == "insert_after"


# ---------------------------------------------------------------------------
# Test: prepare_safe_delete
# ---------------------------------------------------------------------------


class TestCodeEditorSafeDelete:
    def test_safe_delete_preview_returned(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "sd.py"
        _write(target, "def dead():\n    pass\n\ndef live():\n    pass\n")
        original_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)

        pid, diff = editor.prepare_safe_delete(
            name_path      = "dead",
            relative_path  = "sd.py",
            abs_path       = str(target),
            start_line     = 0,
            end_line       = 2,
            original_lines = original_lines,
        )

        assert len(pid) == 12
        assert "dead" in diff
        assert editor.pending_count() == 1

    def test_safe_delete_apply_removes_lines(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "sd_apply.py"
        _write(target, "def dead():\n    pass\n\ndef live():\n    pass\n")
        original_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)

        pid, _ = editor.prepare_safe_delete(
            name_path      = "dead",
            relative_path  = "sd_apply.py",
            abs_path       = str(target),
            start_line     = 0,
            end_line       = 2,
            original_lines = original_lines,
        )
        result = editor.apply(pid)
        assert result["status"] == "applied"
        content = target.read_text(encoding="utf-8")
        assert "def dead" not in content
        assert "def live" in content

    def test_safe_delete_kind_stored(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "sd_kind.py"
        _write(target, "x = 1\n")
        original_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)

        pid, _ = editor.prepare_safe_delete(
            name_path      = "x",
            relative_path  = "sd_kind.py",
            abs_path       = str(target),
            start_line     = 0,
            end_line       = 1,
            original_lines = original_lines,
        )
        entry = editor.get(pid)
        assert entry is not None
        assert entry.kind == "delete"

    def test_safe_delete_stale_detection(self, tmp_path: Path) -> None:
        editor = _make_editor(tmp_path)
        target = tmp_path / "sd_stale.py"
        _write(target, "def dead():\n    pass\n")
        original_lines = target.read_text(encoding="utf-8").splitlines(keepends=True)

        pid, _ = editor.prepare_safe_delete(
            name_path      = "dead",
            relative_path  = "sd_stale.py",
            abs_path       = str(target),
            start_line     = 0,
            end_line       = 2,
            original_lines = original_lines,
        )
        # mutate the file after preview
        _write(target, "def dead():\n    return 99\n")

        with pytest.raises(StalePreviewError) as exc_info:
            editor.apply(pid)
        assert str(target) in exc_info.value.changed_paths


# ---------------------------------------------------------------------------
# Misc: _sha256 helper
# ---------------------------------------------------------------------------


def test_sha256_helper() -> None:
    data = b"hello"
    assert _sha256(data) == hashlib.sha256(data).hexdigest()
