"""Working tree safety of edit checkpoints.

A checkpoint must never take the user's uncommitted work out of the working
tree, and undo must roll back the agent's edit without reaching any file the
edit did not target.

Author: 최진호
Date:   2026-09-10
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

import pytest

from nerdvana_cli.core import checkpoint as checkpoint_module
from nerdvana_cli.core.checkpoint import CheckpointManager, _session_root

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    """Run git inside *repo* and return its stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Git repository with two committed files and an isolated snapshot store."""
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))

    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", str(project)], check=True, capture_output=True)
    _git(project, "config", "user.email", "test@test.com")
    _git(project, "config", "user.name", "Test")

    (project / "target.py").write_text("original target\n")
    (project / "other.py").write_text("original other\n")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")
    return project


def _manager(
    repo:            Path,
    session:         str  = "s-worktree",
    per_session_max: int  = 50,
    enabled:         bool = True,
) -> CheckpointManager:
    return CheckpointManager(
        cwd             = str(repo),
        session_id      = session,
        per_session_max = per_session_max,
        enabled         = enabled,
    )


# ---------------------------------------------------------------------------
# Working tree preservation
# ---------------------------------------------------------------------------

def test_checkpoint_keeps_unstaged_changes_in_worktree(repo: Path) -> None:
    """Unstaged work stays in the working tree when a checkpoint is taken."""
    target = repo / "target.py"
    other  = repo / "other.py"
    target.write_text("user work in progress\n")
    other.write_text("unrelated user work\n")

    before = _git(repo, "status", "--porcelain")

    cp         = _manager(repo)
    checkpoint = cp.before_edit("FileEdit", [str(target)])

    assert checkpoint is not None
    assert target.read_text() == "user work in progress\n"
    assert other.read_text() == "unrelated user work\n"
    assert _git(repo, "status", "--porcelain") == before


def test_checkpoint_never_empties_the_worktree(repo: Path) -> None:
    """No call shape moves uncommitted work out of the tree and into a stash."""
    target    = repo / "target.py"
    untracked = repo / "scratch.txt"
    target.write_text("user work in progress\n")
    untracked.write_text("untracked user work\n")

    cp = _manager(repo)
    cp.before_edit("FileWrite")

    assert target.read_text() == "user work in progress\n"
    assert untracked.read_text() == "untracked user work\n"
    assert _git(repo, "stash", "list") == ""


def test_user_work_survives_the_end_of_the_session(repo: Path) -> None:
    """Work is still in the working tree after the session that checkpointed ends.

    Nothing may be parked in a stash that outlives the session: a later session
    must see the user's uncommitted work exactly where the user left it.
    """
    target    = repo / "target.py"
    untracked = repo / "scratch.txt"
    target.write_text("user work in progress\n")
    untracked.write_text("untracked user work\n")

    first = _manager(repo, session="session-ended")
    first.before_edit("FileEdit", [str(repo / "other.py")])
    del first

    later = _manager(repo, session="session-next")

    assert target.read_text() == "user work in progress\n"
    assert untracked.read_text() == "untracked user work\n"
    assert _git(repo, "stash", "list") == ""
    assert later.list_checkpoints() == []


def test_checkpoint_keeps_untracked_file_in_worktree(repo: Path) -> None:
    """Untracked files survive a checkpoint untouched."""
    untracked = repo / "scratch.txt"
    untracked.write_text("untracked user work\n")

    cp = _manager(repo)
    assert cp.before_edit("FileWrite", [str(repo / "target.py")]) is not None

    assert untracked.is_file()
    assert untracked.read_text() == "untracked user work\n"


def test_checkpoint_keeps_staged_changes_staged(repo: Path) -> None:
    """A staged change is still staged after a checkpoint."""
    target = repo / "other.py"
    target.write_text("staged user work\n")
    _git(repo, "add", "other.py")

    cp = _manager(repo)
    cp.before_edit("FileEdit", [str(repo / "target.py")])

    assert _git(repo, "diff", "--cached", "--name-only").split() == ["other.py"]
    assert target.read_text() == "staged user work\n"


def test_checkpoint_writes_nothing_into_the_project(repo: Path) -> None:
    """Snapshots live outside the project, so git sees no new files."""
    cp = _manager(repo)
    cp.before_edit("FileWrite", [str(repo / "target.py")])

    assert _git(repo, "status", "--porcelain") == ""


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

def test_undo_reverts_edit_and_spares_user_work(repo: Path) -> None:
    """Undo restores the edited file only, leaving user work alone."""
    target    = repo / "target.py"
    other     = repo / "other.py"
    untracked = repo / "scratch.txt"
    other.write_text("uncommitted user work\n")
    untracked.write_text("untracked user work\n")

    cp = _manager(repo)
    cp.before_edit("FileEdit", [str(target)])
    target.write_text("agent output\n")

    message = cp.undo()

    assert "Undone checkpoint" in message
    assert target.read_text() == "original target\n"
    assert other.read_text() == "uncommitted user work\n"
    assert untracked.read_text() == "untracked user work\n"


def test_undo_removes_file_created_by_the_edit(repo: Path) -> None:
    """A file that did not exist before the edit is deleted by undo."""
    created = repo / "generated.py"

    cp = _manager(repo)
    assert cp.before_edit("FileWrite", [str(created)]) is not None
    created.write_text("agent generated\n")

    message = cp.undo()

    assert "Undone checkpoint" in message
    assert not created.exists()


def test_undo_accepts_relative_edit_targets(repo: Path) -> None:
    """Paths are resolved against the manager's cwd."""
    target = repo / "target.py"

    cp = _manager(repo)
    assert cp.before_edit("FileEdit", ["target.py"]) is not None
    target.write_text("agent output\n")
    cp.undo()

    assert target.read_text() == "original target\n"


def test_undo_without_checkpoint_reports_nothing_to_undo(repo: Path) -> None:
    cp = _manager(repo)
    assert cp.undo() == "Nothing to undo."


def test_undo_walks_back_edit_by_edit(repo: Path) -> None:
    """Each undo rolls back one edit, newest first."""
    target = repo / "target.py"

    cp = _manager(repo)
    cp.before_edit("FileEdit", [str(target)])
    target.write_text("first edit\n")
    cp.before_edit("FileEdit", [str(target)])
    target.write_text("second edit\n")

    cp.undo()
    assert target.read_text() == "first edit\n"
    cp.undo()
    assert target.read_text() == "original target\n"


# ---------------------------------------------------------------------------
# Redo
# ---------------------------------------------------------------------------

def test_redo_reapplies_the_undone_edit(repo: Path) -> None:
    """Redo puts the agent's edit back."""
    target = repo / "target.py"

    cp = _manager(repo)
    cp.before_edit("FileEdit", [str(target)])
    target.write_text("agent output\n")
    cp.undo()
    assert target.read_text() == "original target\n"

    message = cp.redo()

    assert "re-applied" in message
    assert target.read_text() == "agent output\n"


def test_redo_leaves_undo_available(repo: Path) -> None:
    """Undo still works after a redo."""
    target = repo / "target.py"

    cp = _manager(repo)
    cp.before_edit("FileEdit", [str(target)])
    target.write_text("agent output\n")
    cp.undo()
    cp.redo()

    cp.undo()
    assert target.read_text() == "original target\n"


def test_redo_reapplies_file_creation(repo: Path) -> None:
    """Redo re-creates a file that undo deleted."""
    created = repo / "generated.py"

    cp = _manager(repo)
    cp.before_edit("FileWrite", [str(created)])
    created.write_text("agent generated\n")
    cp.undo()
    assert not created.exists()

    cp.redo()

    assert created.read_text() == "agent generated\n"


def test_redo_without_undo_reports_nothing_to_redo(repo: Path) -> None:
    cp = _manager(repo)
    assert cp.redo() == "Nothing to redo."


def test_new_edit_invalidates_pending_redo(repo: Path) -> None:
    """A fresh checkpoint drops the redo history."""
    target = repo / "target.py"

    cp = _manager(repo)
    cp.before_edit("FileEdit", [str(target)])
    target.write_text("agent output\n")
    cp.undo()
    cp.before_edit("FileEdit", [str(target)])

    assert cp.redo() == "Nothing to redo."


# ---------------------------------------------------------------------------
# Listing, bounds and skips
# ---------------------------------------------------------------------------

def test_list_checkpoints_reports_captured_paths(repo: Path) -> None:
    target = repo / "target.py"

    cp = _manager(repo)
    checkpoint = cp.before_edit("FileEdit", [str(target)])
    entries    = cp.list_checkpoints()

    assert [e.checkpoint_id for e in entries] == [checkpoint]
    assert entries[0].paths == (str(target),)
    assert entries[0].stash_ref == checkpoint


def test_old_checkpoints_are_evicted(repo: Path) -> None:
    """Snapshot count per session stays within per_session_max."""
    cp = _manager(repo, per_session_max=2)
    for index in range(4):
        cp.before_edit("FileWrite", [str(repo / f"file{index}.py")])

    entries = cp.list_checkpoints()

    assert len(entries) == 2
    assert [e.edit_id for e in entries] == [3, 4]


def test_oversized_file_is_skipped_and_left_alone(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file above the size cap is not copied, and undo leaves it untouched."""
    monkeypatch.setattr(checkpoint_module, "_MAX_FILE_BYTES", 8)
    target = repo / "target.py"

    cp = _manager(repo)
    assert cp.before_edit("FileEdit", [str(target)]) is not None
    target.write_text("agent output\n")

    message = cp.undo()

    assert "skipped" in message
    assert target.read_text() == "agent output\n"


def test_edit_target_count_is_capped(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the first N edit targets are captured."""
    monkeypatch.setattr(checkpoint_module, "_MAX_FILES_PER_EDIT", 2)

    cp = _manager(repo)
    cp.before_edit("FileWrite", [str(repo / f"file{i}.py") for i in range(5)])

    assert len(cp.list_checkpoints()[0].paths) == 2


def test_before_edit_without_targets_is_a_no_op(repo: Path) -> None:
    """No edit targets means no snapshot and no working tree change."""
    target = repo / "target.py"
    target.write_text("user work in progress\n")

    cp = _manager(repo)

    assert cp.before_edit("FileWrite") is None
    assert cp.list_checkpoints() == []
    assert target.read_text() == "user work in progress\n"


def test_a_caller_that_names_no_file_is_reported(repo: Path, caplog: pytest.LogCaptureFixture) -> None:
    """An inert checkpoint call is warned about once, not hidden in debug logs."""
    cp = _manager(repo)

    with caplog.at_level(logging.WARNING, logger="nerdvana_cli.core.checkpoint"):
        cp.before_edit("FileWrite")
        cp.before_edit("FileEdit")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]

    assert len(warnings) == 1
    assert "undo has nothing to restore" in warnings[0].getMessage()


def test_disabled_manager_captures_nothing(repo: Path) -> None:
    cp = _manager(repo, enabled=False)

    assert cp.before_edit("FileEdit", [str(repo / "target.py")]) is None
    assert cp.list_checkpoints() == []


def test_stale_session_store_is_reclaimed(repo: Path) -> None:
    """A session directory untouched past the TTL is dropped on the next capture."""
    stale = _session_root("session-from-april")
    stale.mkdir(parents=True)
    aged = time.time() - checkpoint_module._SESSION_TTL_SECS - 60
    os.utime(stale, (aged, aged))

    cp = _manager(repo, session="session-now")
    cp.before_edit("FileEdit", [str(repo / "target.py")])

    assert not stale.exists()
    assert _session_root("session-now").is_dir()


def test_legacy_stashes_are_listed_and_never_dropped(repo: Path) -> None:
    """Stashes from the earlier build stay put and stay visible.

    They may hold the only copy of work an earlier build removed from a working
    tree, so nothing here may delete them.
    """
    (repo / "other.py").write_text("work an earlier build hid\n")
    _git(repo, "stash", "push", "--message", "nerdvana:82f02308:1")
    stashed = _git(repo, "stash", "list")
    assert "nerdvana:82f02308:1" in stashed

    cp = _manager(repo, session="session-now")
    cp.before_edit("FileEdit", [str(repo / "target.py")])
    cp.undo()

    entries = cp.list_checkpoints()
    legacy  = [e for e in entries if e.kind == "legacy-stash"]

    assert [e.edit_id for e in legacy] == [1]
    assert legacy[0].checkpoint_id == "stash@{0}"
    assert _git(repo, "stash", "list") == stashed


def test_sessions_do_not_share_checkpoints(repo: Path) -> None:
    """Two sessions in the same repository keep separate snapshots."""
    first  = _manager(repo, session="session-a")
    second = _manager(repo, session="session-b")

    first.before_edit("FileEdit", [str(repo / "target.py")])

    assert len(first.list_checkpoints()) == 1
    assert second.list_checkpoints() == []
