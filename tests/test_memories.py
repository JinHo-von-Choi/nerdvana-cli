"""Tests for MemoriesManager — Phase E.

Covers: CRUD, scope routing, fcntl locking (smoke), stale listing,
        onboarding helpers, session_start_hint.

Author: 최진호
Date:   2026-04-18
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from nerdvana_cli.core.memories import MemoriesManager, MemoryEntry, MemoryScope


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project_dir(tmp_path: Path) -> str:
    """Temporary project directory."""
    return str(tmp_path / "project")


@pytest.fixture
def mgr(project_dir: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoriesManager:
    os.makedirs(project_dir, exist_ok=True)
    # Isolate user_global memories to the tmp path so tests never see the
    # real ~/.nerdvana/memories/global/ directory on the developer's box.
    fake_global = tmp_path / "global"
    fake_global.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "nerdvana_cli.core.memories.core_paths.global_memories_dir",
        lambda: fake_global,
    )
    return MemoriesManager(project_dir)


# ---------------------------------------------------------------------------
# MemoryScope enum
# ---------------------------------------------------------------------------

def test_scope_enum_values() -> None:
    assert MemoryScope.PROJECT_RULE      == "project_rule"
    assert MemoryScope.PROJECT_KNOWLEDGE == "project_knowledge"
    assert MemoryScope.USER_GLOBAL       == "user_global"
    assert MemoryScope.AGENT_EXPERIENCE  == "agent_experience"


# ---------------------------------------------------------------------------
# Write → Read  (project_knowledge)
# ---------------------------------------------------------------------------

def test_write_read_project_knowledge(mgr: MemoriesManager) -> None:
    mgr.write("build-commands", "pytest tests/ -q", MemoryScope.PROJECT_KNOWLEDGE)
    content = mgr.read("build-commands")
    assert content == "pytest tests/ -q"


def test_write_read_slash_namespace(mgr: MemoriesManager) -> None:
    mgr.write("auth/login/rules", "Always validate JWT", MemoryScope.PROJECT_KNOWLEDGE)
    content = mgr.read("auth/login/rules")
    assert "JWT" in content


def test_write_overwrite(mgr: MemoriesManager) -> None:
    mgr.write("note", "v1", MemoryScope.PROJECT_KNOWLEDGE)
    mgr.write("note", "v2", MemoryScope.PROJECT_KNOWLEDGE)
    assert mgr.read("note") == "v2"


# ---------------------------------------------------------------------------
# Write → user_global (different path)
# ---------------------------------------------------------------------------

def test_write_user_global(mgr: MemoriesManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    global_dir = tmp_path / "global_mem"
    monkeypatch.setattr(
        "nerdvana_cli.core.memories.core_paths.global_memories_dir",
        lambda: global_dir,
    )
    mgr2 = MemoriesManager(str(tmp_path / "project"))
    mgr2.write("global-pref", "dark mode", MemoryScope.USER_GLOBAL)
    assert (global_dir / "global-pref.md").exists()
    content = mgr2.read("global-pref")
    assert "dark mode" in content


# ---------------------------------------------------------------------------
# Scope routing: agent_experience raises NotImplementedError
# ---------------------------------------------------------------------------

def test_agent_experience_raises(mgr: MemoriesManager) -> None:
    with pytest.raises(NotImplementedError, match="AnchorMind"):
        mgr.write("exp", "some error", MemoryScope.AGENT_EXPERIENCE)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def test_delete_existing(mgr: MemoriesManager) -> None:
    mgr.write("to-delete", "bye", MemoryScope.PROJECT_KNOWLEDGE)
    result = mgr.delete("to-delete")
    assert "Deleted" in result
    with pytest.raises(FileNotFoundError):
        mgr.read("to-delete")


def test_delete_missing_raises(mgr: MemoriesManager) -> None:
    with pytest.raises(FileNotFoundError):
        mgr.delete("nonexistent")


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------

def test_rename_same_scope(mgr: MemoriesManager) -> None:
    mgr.write("old-name", "hello", MemoryScope.PROJECT_KNOWLEDGE)
    mgr.rename("old-name", "new-name")
    assert mgr.read("new-name") == "hello"
    with pytest.raises(FileNotFoundError):
        mgr.read("old-name")


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

def test_edit_literal(mgr: MemoriesManager) -> None:
    mgr.write("doc", "Hello World", MemoryScope.PROJECT_KNOWLEDGE)
    mgr.edit("doc", "World", "Python", mode="literal")
    assert mgr.read("doc") == "Hello Python"


def test_edit_regex(mgr: MemoriesManager) -> None:
    mgr.write("code", "v1.0 and v2.0", MemoryScope.PROJECT_KNOWLEDGE)
    mgr.edit("code", r"v(\d+\.\d+)", r"version-\1", mode="regex")
    content = mgr.read("code")
    assert "version-1.0" in content
    assert "version-2.0" in content


def test_edit_invalid_mode(mgr: MemoriesManager) -> None:
    mgr.write("m", "x", MemoryScope.PROJECT_KNOWLEDGE)
    with pytest.raises(ValueError, match="mode"):
        mgr.edit("m", "x", "y", mode="invalid")


def test_edit_missing_raises(mgr: MemoriesManager) -> None:
    with pytest.raises(FileNotFoundError):
        mgr.edit("ghost", "a", "b")


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def test_list_empty(mgr: MemoriesManager) -> None:
    assert mgr.list_memories() == []


def test_list_multiple(mgr: MemoriesManager) -> None:
    mgr.write("alpha", "a", MemoryScope.PROJECT_KNOWLEDGE)
    mgr.write("beta",  "b", MemoryScope.PROJECT_KNOWLEDGE)
    entries = mgr.list_memories()
    names = [e.name for e in entries]
    assert "alpha" in names
    assert "beta" in names


def test_list_returns_memory_entry(mgr: MemoriesManager) -> None:
    mgr.write("entry-check", "content", MemoryScope.PROJECT_KNOWLEDGE)
    entries = mgr.list_memories()
    assert len(entries) >= 1
    e = entries[0]
    assert isinstance(e, MemoryEntry)
    assert e.size > 0
    assert e.mtime > 0


def test_list_topic_filter(mgr: MemoriesManager) -> None:
    mgr.write("auth/login", "jwt", MemoryScope.PROJECT_KNOWLEDGE)
    mgr.write("db/schema",  "pg",  MemoryScope.PROJECT_KNOWLEDGE)
    auth_entries = mgr.list_memories(topic="auth")
    names = [e.name for e in auth_entries]
    assert "auth/login" in names
    assert "db/schema" not in names


# ---------------------------------------------------------------------------
# Stale GC list
# ---------------------------------------------------------------------------

def test_list_stale_empty_when_fresh(mgr: MemoriesManager) -> None:
    mgr.write("fresh", "now", MemoryScope.PROJECT_KNOWLEDGE)
    stale = mgr.list_stale(days=30)
    assert not any(e.name == "fresh" for e in stale)


def test_list_stale_detects_old(mgr: MemoriesManager, tmp_path: Path) -> None:
    mgr.write("old-mem", "data", MemoryScope.PROJECT_KNOWLEDGE)
    # Backdate the file
    from nerdvana_cli.core import paths as core_paths
    p = core_paths.project_memories_dir(mgr._cwd) / "old-mem.md"
    old_time = time.time() - 40 * 86_400  # 40 days ago
    os.utime(p, (old_time, old_time))
    stale = mgr.list_stale(days=30)
    assert any(e.name == "old-mem" for e in stale)


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------

def test_onboarding_not_done(mgr: MemoriesManager) -> None:
    assert mgr.onboarding_exists() is False


def test_onboarding_mark_done(mgr: MemoriesManager) -> None:
    mgr.mark_onboarding_done()
    assert mgr.onboarding_exists() is True


# ---------------------------------------------------------------------------
# Session start hint
# ---------------------------------------------------------------------------

def test_session_hint_empty(mgr: MemoriesManager) -> None:
    assert mgr.session_start_hint() == ""


def test_session_hint_with_memories(mgr: MemoriesManager) -> None:
    mgr.write("hint-test", "data", MemoryScope.PROJECT_KNOWLEDGE)
    hint = mgr.session_start_hint()
    assert "ListMemories" in hint
    assert "1" in hint


# ---------------------------------------------------------------------------
# Invalid name validation
# ---------------------------------------------------------------------------

def test_invalid_name_raises(mgr: MemoriesManager) -> None:
    with pytest.raises(ValueError, match="invalid"):
        mgr.write("../../etc/passwd", "evil", MemoryScope.PROJECT_KNOWLEDGE)
