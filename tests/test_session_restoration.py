"""Tests for optimized session restoration."""
from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core.session import SessionStorage


class TestSessionRestoration:
    """Session restoration with summary optimization."""

    def test_save_and_get_summary(self, tmp_path: Path):
        storage = SessionStorage(storage_dir=str(tmp_path))
        session_id = storage.session_id

        storage.save_summary(session_id, "User greeted, assistant responded")
        summary = storage.get_summary(session_id)

        assert summary == "User greeted, assistant responded"

    def test_get_summary_returns_empty_when_none(self, tmp_path: Path):
        storage = SessionStorage(storage_dir=str(tmp_path))
        assert storage.get_summary("nonexistent") == ""

    def test_restore_with_summary(self, tmp_path: Path):
        storage = SessionStorage(storage_dir=str(tmp_path))
        session_id = storage.session_id

        storage.record(session_id, {"role": "user", "content": "Hello"})
        storage.record(session_id, {"role": "assistant", "content": "Hi there"})
        storage.save_summary(session_id, "User greeted, assistant responded")

        context = storage.restore_with_summary()
        assert "User greeted" in context
        assert "Hello" not in context

    def test_restore_fallback_to_recent_messages(self, tmp_path: Path):
        storage = SessionStorage(storage_dir=str(tmp_path))
        session_id = storage.session_id

        storage.record(session_id, {"role": "user", "content": "Test message"})
        context = storage.restore_with_summary()

        assert "Test message" in context
