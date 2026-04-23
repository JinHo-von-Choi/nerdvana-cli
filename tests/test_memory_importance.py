"""Tests for memory importance tracking and decay."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from nerdvana_cli.core.memories import MemoryEntry, MemoryScope, MemoriesManager


class TestMemoryImportance:
    """Memory importance scoring and retention."""

    def test_entry_has_default_importance(self):
        entry = MemoryEntry(
            name="test",
            scope=MemoryScope.PROJECT_KNOWLEDGE,
            size=100,
            mtime=time.time(),
        )
        assert entry.importance == 0.5

    def test_entry_with_custom_importance(self):
        entry = MemoryEntry(
            name="important",
            scope=MemoryScope.PROJECT_KNOWLEDGE,
            size=100,
            mtime=time.time(),
            importance=0.9,
        )
        assert entry.importance == 0.9

    def test_list_stale_returns_old_entries(self, tmp_path: Path):
        manager = MemoriesManager(cwd=str(tmp_path))
        manager.write("old_memory", "old content", scope=MemoryScope.PROJECT_KNOWLEDGE)

        stale = manager.list_stale(days=0)
        assert len(stale) >= 1

    def test_list_stale_empty_when_all_recent(self, tmp_path: Path):
        manager = MemoriesManager(cwd=str(tmp_path))
        manager.write("new_memory", "new content", scope=MemoryScope.PROJECT_KNOWLEDGE)

        stale = manager.list_stale(days=365)
        assert len(stale) == 0
