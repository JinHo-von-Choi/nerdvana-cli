"""Containment checks for memory name to filesystem path resolution.

A memory name is attacker-influenced input: it arrives through the
WriteMemory/ReadMemory tool surface. These tests pin the guarantee that a
name can never designate a file outside its scope directory, and that the
slash-namespacing feature keeps working.

Author: 최진호
Date:   2026-09-10
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nerdvana_cli.core.memories import MemoriesManager, MemoryScope

pytestmark = pytest.mark.security


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture()
def manager(
    project_root:  Path,
    tmp_path:      Path,
    monkeypatch:   pytest.MonkeyPatch,
) -> MemoriesManager:
    """MemoriesManager whose project and global scopes both live under tmp_path."""
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    return MemoriesManager(cwd=str(project_root))


def test_absolute_name_is_rejected_and_writes_nothing(manager: MemoriesManager) -> None:
    """A rooted name must not be able to plant a file at an arbitrary location."""
    victim = Path("/etc/cron.d/nerdvana_pwned.md")

    with pytest.raises(ValueError, match="absolute"):
        manager.write("/etc/cron.d/nerdvana_pwned", "* * * * * root id", MemoryScope.PROJECT_KNOWLEDGE)

    assert not victim.exists()


def test_absolute_name_is_rejected_in_global_scope(
    manager:   MemoriesManager,
    tmp_path:  Path,
) -> None:
    """The same guard applies to the user-global scope, not just the project scope."""
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "planted"

    with pytest.raises(ValueError, match="absolute"):
        manager.write(str(target), "payload", MemoryScope.USER_GLOBAL)

    assert list(outside.iterdir()) == []


def test_absolute_name_is_rejected_on_read(manager: MemoriesManager) -> None:
    """Reads are guarded too, so a rooted name cannot exfiltrate a system file."""
    with pytest.raises(ValueError, match="absolute"):
        manager.read("/etc/hostname")


def test_slash_namespaced_name_still_round_trips(
    manager:       MemoriesManager,
    project_root:  Path,
) -> None:
    """Sub-directory namespaces are a supported feature and must keep working."""
    manager.write("notes/todo", "ship the patch", MemoryScope.PROJECT_KNOWLEDGE)

    assert manager.read("notes/todo") == "ship the patch"
    assert (project_root / ".nerdvana" / "memories" / "notes" / "todo.md").is_file()
    assert [entry.name for entry in manager.list_memories()] == ["notes/todo"]


def test_parent_traversal_is_rejected_and_writes_nothing(
    manager:       MemoriesManager,
    project_root:  Path,
) -> None:
    """Dot-dot segments must not climb out of the scope directory."""
    with pytest.raises(ValueError, match=r"\.\."):
        manager.write("../../escaped", "payload", MemoryScope.PROJECT_KNOWLEDGE)

    assert not (project_root / "escaped.md").exists()


def test_symlinked_component_escape_is_rejected_and_writes_nothing(
    manager:       MemoriesManager,
    project_root:  Path,
    tmp_path:      Path,
) -> None:
    """A symlink planted inside the scope must not become an exit."""
    outside = tmp_path / "outside"
    outside.mkdir()
    memories = project_root / ".nerdvana" / "memories"
    memories.mkdir(parents=True)
    (memories / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside working directory"):
        manager.write("escape/planted", "payload", MemoryScope.PROJECT_KNOWLEDGE)

    assert list(outside.iterdir()) == []
