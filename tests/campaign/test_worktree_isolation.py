"""A campaign worktree is a checkout of its own: the repository never sees the work done in it.

작성자: 최진호
날짜: 2026-10-05

The pool checks a repository out on a branch of its own and takes the
checkout down again, so a task that runs in a worktree leaves the repository
it came from exactly as it was.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from nerdvana_cli.core.campaign.worktree_pool import WorktreeError, WorktreePool


def test_a_worktree_is_its_own_checkout_and_the_repository_never_changes(
    git_repo: Path,
    tmp_path: Path,
    git: Callable[..., str],
) -> None:
    pool = WorktreePool(base_dir=tmp_path / "pool")

    session = pool.create_worktree(git_repo, "campaign/alpha-1")

    assert session.active is True
    assert session.branch_name == "campaign/alpha-1"
    assert session.commit == git(git_repo, "rev-parse", "HEAD")
    assert (session.worktree_path / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert git(git_repo, "status", "--porcelain") == ""
    assert git(git_repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert "campaign/alpha-1" in git(git_repo, "branch", "--list", "campaign/alpha-1")

    (session.worktree_path / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    (session.worktree_path / "added.txt").write_text("x\n", encoding="utf-8")

    assert (git_repo / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (git_repo / "added.txt").exists()
    assert git(git_repo, "status", "--porcelain") == ""

    pool.remove_worktree(session)

    assert session.active is False
    assert not session.worktree_path.exists()
    assert git(git_repo, "status", "--porcelain") == ""


def test_removing_a_worktree_takes_the_checkout_and_its_edits_with_it(
    git_repo: Path,
    tmp_path: Path,
    git: Callable[..., str],
) -> None:
    pool = WorktreePool(base_dir=tmp_path / "pool")
    session = pool.create_worktree(git_repo, "campaign/alpha-2")
    (session.worktree_path / "draft.txt").write_text("unfinished\n", encoding="utf-8")

    pool.remove_worktree(session)

    assert session.active is False
    assert not session.worktree_path.exists()
    assert not (git_repo / "draft.txt").exists()
    assert git(git_repo, "status", "--porcelain") == ""
    assert "campaign/alpha-2" in git(git_repo, "branch", "--list", "campaign/alpha-2")


def test_a_worktree_is_taken_down_even_when_the_block_raises(
    git_repo: Path,
    tmp_path: Path,
    git: Callable[..., str],
) -> None:
    pool = WorktreePool(base_dir=tmp_path / "pool")
    seen: list[Path] = []

    with (
        pytest.raises(RuntimeError, match="verification crashed"),
        pool.isolated_worktree(git_repo, "campaign/alpha-3") as session,
    ):
        seen.append(session.worktree_path)
        raise RuntimeError("verification crashed")

    assert seen != []
    assert not seen[0].exists()
    assert git(git_repo, "status", "--porcelain") == ""


def test_a_pool_without_a_base_directory_still_cleans_up_after_itself(
    git_repo: Path,
    git: Callable[..., str],
) -> None:
    pool = WorktreePool()

    with pool.isolated_worktree(git_repo, "campaign/alpha-4") as session:
        parent = session.worktree_path.parent
        assert session.worktree_path.is_dir()

    assert not session.worktree_path.exists()
    assert not parent.exists()
    assert git(git_repo, "status", "--porcelain") == ""


def test_two_worktrees_of_one_repository_never_share_a_branch(
    git_repo: Path,
    tmp_path: Path,
    git: Callable[..., str],
) -> None:
    pool = WorktreePool(base_dir=tmp_path / "pool")
    elsewhere = WorktreePool()

    with pool.isolated_worktree(git_repo, "campaign/alpha-5") as first:
        with pytest.raises(WorktreeError, match="campaign/alpha-5"):
            elsewhere.create_worktree(git_repo, "campaign/alpha-5")
        assert first.worktree_path.is_dir()

    assert "campaign/alpha-5" in git(git_repo, "branch", "--list", "campaign/alpha-5")
    assert git(git_repo, "worktree", "list", "--porcelain").count("worktree ") == 1
