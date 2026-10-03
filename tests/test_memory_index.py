"""Timestamps, source and load tracking of memory entries.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from nerdvana_cli.core.memories import MemoriesManager, MemoryScope
from nerdvana_cli.core.memory_index import (
    INDEX_NAME,
    MemoryIndex,
    MemoryMeta,
    MemorySource,
    content_hash,
    effective_modified,
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def mgr(project: Path) -> MemoriesManager:
    return MemoriesManager(str(project))


def _entry(mgr: MemoriesManager, name: str):
    return next(e for e in mgr.list_memories() if e.name == name)


class TestRecordedOnWrite:
    def test_new_entry_has_created_modified_and_user_source(self, mgr: MemoriesManager) -> None:
        before = time.time()
        mgr.write("notes", "alpha", MemoryScope.PROJECT_KNOWLEDGE)
        entry = _entry(mgr, "notes")
        assert before - 1 <= entry.created <= time.time() + 1
        assert entry.mtime >= entry.created
        assert entry.source == "user"
        assert entry.loaded_at is None

    def test_agent_source_is_recorded(self, mgr: MemoriesManager) -> None:
        mgr.write("notes", "alpha", MemoryScope.PROJECT_KNOWLEDGE, source=MemorySource.AGENT)
        assert _entry(mgr, "notes").source == "agent"

    def test_import_source_is_recorded(self, mgr: MemoriesManager) -> None:
        mgr.write("notes", "alpha", MemoryScope.USER_GLOBAL, source=MemorySource.IMPORT)
        assert _entry(mgr, "notes").source == "import"

    def test_overwrite_keeps_created_and_updates_modified(self, mgr: MemoriesManager, project: Path) -> None:
        mgr.write("notes", "alpha", MemoryScope.PROJECT_KNOWLEDGE)
        index = MemoryIndex(project / ".nerdvana" / "memories")
        first = index.get("notes")
        assert first is not None
        time.sleep(0.02)
        mgr.write("notes", "beta", MemoryScope.PROJECT_KNOWLEDGE, source=MemorySource.AGENT)
        second = index.get("notes")
        assert second is not None
        assert second.created == first.created
        assert second.modified > first.modified
        assert second.source == "agent"
        assert second.sha256 == content_hash("beta")

    def test_edit_keeps_the_original_source(self, mgr: MemoriesManager, project: Path) -> None:
        mgr.write("notes", "alpha", MemoryScope.PROJECT_KNOWLEDGE, source=MemorySource.USER)
        mgr.edit("notes", "alpha", "gamma")
        meta = MemoryIndex(project / ".nerdvana" / "memories").get("notes")
        assert meta is not None
        assert meta.source == "user"
        assert meta.sha256 == content_hash("gamma")

    def test_rename_carries_created_and_source_over(self, mgr: MemoriesManager) -> None:
        mgr.write("old", "alpha", MemoryScope.PROJECT_KNOWLEDGE, source=MemorySource.AGENT)
        created = _entry(mgr, "old").created
        mgr.rename("old", "new")
        entry = _entry(mgr, "new")
        assert entry.created == created
        assert entry.source == "agent"
        assert all(e.name != "old" for e in mgr.list_memories())

    def test_rename_across_scopes_moves_the_record(self, mgr: MemoriesManager) -> None:
        mgr.write("old", "alpha", MemoryScope.PROJECT_KNOWLEDGE, source=MemorySource.IMPORT)
        mgr.rename("old", "new", MemoryScope.USER_GLOBAL)
        entry = _entry(mgr, "new")
        assert entry.scope == MemoryScope.USER_GLOBAL
        assert entry.source == "import"

    def test_delete_drops_the_record(self, mgr: MemoriesManager, project: Path) -> None:
        mgr.write("notes", "alpha", MemoryScope.PROJECT_KNOWLEDGE)
        mgr.delete("notes")
        assert MemoryIndex(project / ".nerdvana" / "memories").get("notes") is None

    def test_name_with_md_suffix_shares_one_record(self, mgr: MemoriesManager) -> None:
        mgr.write("notes.md", "alpha", MemoryScope.PROJECT_KNOWLEDGE, source=MemorySource.AGENT)
        assert _entry(mgr, "notes").source == "agent"

    def test_memory_files_stay_plain_text(self, mgr: MemoriesManager, project: Path) -> None:
        mgr.write("notes", "alpha", MemoryScope.PROJECT_KNOWLEDGE)
        assert (project / ".nerdvana" / "memories" / "notes.md").read_text(encoding="utf-8") == "alpha"
        assert (project / ".nerdvana" / "memories" / INDEX_NAME).is_file()


class TestLegacyEntries:
    def test_entry_without_a_record_reports_its_mtime_and_unknown_source(
        self, mgr: MemoriesManager, project: Path,
    ) -> None:
        path = project / ".nerdvana" / "memories" / "legacy.md"
        path.parent.mkdir(parents=True)
        path.write_text("old note", encoding="utf-8")
        stamp = time.time() - 5 * 86400
        os.utime(path, (stamp, stamp))
        entry = _entry(mgr, "legacy")
        assert entry.mtime == pytest.approx(stamp)
        assert entry.created == pytest.approx(stamp)
        assert entry.source == "unknown"

    def test_reading_a_legacy_entry_creates_its_record_with_the_file_time(
        self, mgr: MemoriesManager, project: Path,
    ) -> None:
        path = project / ".nerdvana" / "memories" / "legacy.md"
        path.parent.mkdir(parents=True)
        path.write_text("old note", encoding="utf-8")
        stamp = time.time() - 5 * 86400
        os.utime(path, (stamp, stamp))
        assert mgr.read("legacy") == "old note"
        entry = _entry(mgr, "legacy")
        assert entry.loaded_at is not None
        assert entry.mtime == pytest.approx(stamp)


class TestLoadTracking:
    def test_read_records_a_load(self, mgr: MemoriesManager) -> None:
        mgr.write("notes", "alpha", MemoryScope.PROJECT_KNOWLEDGE)
        assert _entry(mgr, "notes").loaded_at is None
        before = time.time()
        assert mgr.read("notes") == "alpha"
        loaded = _entry(mgr, "notes").loaded_at
        assert loaded is not None and loaded >= before - 1

    def test_peek_does_not_record_a_load(self, mgr: MemoriesManager) -> None:
        mgr.write("notes", "alpha", MemoryScope.PROJECT_KNOWLEDGE)
        assert mgr.peek("notes", MemoryScope.PROJECT_KNOWLEDGE) == "alpha"
        assert _entry(mgr, "notes").loaded_at is None

    def test_rename_does_not_count_as_a_load(self, mgr: MemoriesManager) -> None:
        mgr.write("old", "alpha", MemoryScope.PROJECT_KNOWLEDGE)
        mgr.rename("old", "new")
        assert _entry(mgr, "new").loaded_at is None

    def test_edit_does_not_count_as_a_load(self, mgr: MemoriesManager) -> None:
        mgr.write("notes", "alpha", MemoryScope.PROJECT_KNOWLEDGE)
        mgr.edit("notes", "alpha", "beta")
        assert _entry(mgr, "notes").loaded_at is None

    def test_a_write_keeps_the_last_load(self, mgr: MemoriesManager) -> None:
        mgr.write("notes", "alpha", MemoryScope.PROJECT_KNOWLEDGE)
        mgr.read("notes")
        loaded = _entry(mgr, "notes").loaded_at
        mgr.write("notes", "beta", MemoryScope.PROJECT_KNOWLEDGE)
        assert _entry(mgr, "notes").loaded_at == loaded


class TestStaleListing:
    def _age(self, project: Path, name: str, days: int) -> None:
        """Make a written entry look *days* days old: record and file time both move back."""
        base    = project / ".nerdvana" / "memories"
        records = MemoryIndex(base).records()
        past    = time.time() - days * 86400
        records[name] = replace(records[name], created=past, modified=past)
        (base / INDEX_NAME).write_text(json.dumps({n: asdict(m) for n, m in records.items()}), encoding="utf-8")
        os.utime(base / f"{name}.md", (past, past))

    def test_never_loaded_filter_drops_entries_that_were_read(self, mgr: MemoriesManager, project: Path) -> None:
        mgr.write("read-me", "a", MemoryScope.PROJECT_KNOWLEDGE)
        mgr.write("ignored", "b", MemoryScope.PROJECT_KNOWLEDGE)
        mgr.read("read-me")
        self._age(project, "read-me", 60)
        self._age(project, "ignored", 60)
        assert {e.name for e in mgr.list_stale(days=30)} == {"read-me", "ignored"}
        assert {e.name for e in mgr.list_stale(days=30, never_loaded=True)} == {"ignored"}

    def test_recent_entries_are_not_stale(self, mgr: MemoriesManager) -> None:
        mgr.write("fresh", "a", MemoryScope.PROJECT_KNOWLEDGE)
        assert mgr.list_stale(days=30, never_loaded=True) == []


class TestEffectiveModified:
    def _meta(self, modified: float, content: str) -> MemoryMeta:
        return MemoryMeta(created=modified, modified=modified, source="user", sha256=content_hash(content))

    def test_untouched_file_reports_the_recorded_time(self, tmp_path: Path) -> None:
        path = tmp_path / "a.md"
        path.write_text("x", encoding="utf-8")
        assert effective_modified(self._meta(1000.0, "x"), path, 1000.5) == 1000.0

    def test_checkout_that_resets_mtime_keeps_the_recorded_time(self, tmp_path: Path) -> None:
        path = tmp_path / "a.md"
        path.write_text("x", encoding="utf-8")
        assert effective_modified(self._meta(1000.0, "x"), path, 5000.0) == 1000.0

    def test_edit_behind_the_index_reports_the_file_time(self, tmp_path: Path) -> None:
        path = tmp_path / "a.md"
        path.write_text("changed", encoding="utf-8")
        assert effective_modified(self._meta(1000.0, "x"), path, 5000.0) == 5000.0

    def test_older_file_time_is_taken_as_given(self, tmp_path: Path) -> None:
        path = tmp_path / "a.md"
        path.write_text("x", encoding="utf-8")
        assert effective_modified(self._meta(5000.0, "x"), path, 1000.0) == 1000.0

    def test_no_record_reports_the_file_time(self, tmp_path: Path) -> None:
        assert effective_modified(None, tmp_path / "a.md", 42.0) == 42.0


class TestIndexFile:
    def test_unreadable_index_reads_as_empty_and_is_rebuilt_on_write(self, mgr: MemoriesManager, project: Path) -> None:
        base = project / ".nerdvana" / "memories"
        base.mkdir(parents=True)
        (base / INDEX_NAME).write_text("{not json", encoding="utf-8")
        assert MemoryIndex(base).records() == {}
        mgr.write("notes", "alpha", MemoryScope.PROJECT_KNOWLEDGE)
        assert MemoryIndex(base).get("notes") is not None

    def test_index_file_is_not_listed_as_a_memory(self, mgr: MemoriesManager) -> None:
        mgr.write("notes", "alpha", MemoryScope.PROJECT_KNOWLEDGE)
        assert [e.name for e in mgr.list_memories()] == ["notes"]
