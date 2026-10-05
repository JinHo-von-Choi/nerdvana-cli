"""Isolated git worktrees for the repositories a campaign touches.

작성자: 최진호
날짜: 2026-10-05

A campaign task runs in a worktree of its own: the pool checks the repository
out on a fresh branch, the verifier is handed that path, and the pool takes the
worktree down again whether the verification passed or failed. The repository
the campaign started from is only ever read.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

WORKTREE_PREFIX = "nerdvana-campaign-"


class WorktreeError(RuntimeError):
    """A worktree could not be created or removed."""


@dataclass
class WorktreeSession:
    """One checkout: where it came from, where it lives, its branch and the commit it starts from."""

    repo_path: Path
    worktree_path: Path
    branch_name: str
    commit: str
    active: bool = True


class WorktreePool:
    """Creates worktrees on branches of their own and removes them again."""

    def __init__(self, base_dir: Path | None = None) -> None:
        """Worktrees go under ``base_dir`` when it is given, into a fresh temporary directory when it is not."""
        self.base_dir = base_dir

    def create_worktree(self, repo_path: Path, branch_name: str, base_commit: str = "HEAD") -> WorktreeSession:
        """A worktree of ``repo_path``, checked out on ``branch_name`` at ``base_commit``.

        Raises ``WorktreeError`` when git refuses the checkout and leaves nothing behind.
        """
        root = repo_path.resolve()
        try:
            commit = _run(root, ["git", "rev-parse", base_commit]).stdout.strip()
        except subprocess.CalledProcessError as error:
            raise WorktreeError(_failure("rev-parse", base_commit, error)) from error
        worktree_path = self._reserve_path(branch_name)
        try:
            _run(root, ["git", "worktree", "add", "-b", branch_name, str(worktree_path), base_commit])
        except subprocess.CalledProcessError as error:
            self._discard(worktree_path)
            raise WorktreeError(_failure("worktree add", branch_name, error)) from error
        return WorktreeSession(repo_path=root, worktree_path=worktree_path, branch_name=branch_name, commit=commit)

    def remove_worktree(self, session: WorktreeSession, force: bool = True) -> None:
        """Take the checkout down: git removes it, anything left is swept away, the session is marked gone.

        Without ``force`` a dirty worktree is refused with ``WorktreeError`` and left alone.
        """
        if not session.active:
            return
        flag = ["--force"] if force else []
        done = _run(
            session.repo_path,
            ["git", "worktree", "remove", *flag, str(session.worktree_path)],
            check=False,
        )
        if done.returncode != 0 and not force:
            detail = (done.stderr or "").strip() or f"exit status {done.returncode}"
            raise WorktreeError(f"worktree remove refused for {session.branch_name}: {detail}")
        self._discard(session.worktree_path)
        session.active = False

    @contextmanager
    def isolated_worktree(
        self,
        repo_path: Path,
        branch_name: str,
        base_commit: str = "HEAD",
    ) -> Iterator[WorktreeSession]:
        """A worktree that is taken down again when the block ends, raised or not."""
        session = self.create_worktree(repo_path, branch_name, base_commit)
        try:
            yield session
        finally:
            self.remove_worktree(session, force=True)

    def _reserve_path(self, branch_name: str) -> Path:
        """Where the worktree goes: git creates the leaf, only its parent is prepared here."""
        if self.base_dir is None:
            return Path(tempfile.mkdtemp(prefix=WORKTREE_PREFIX)) / "work"
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", branch_name).strip("-") or "worktree"
        worktree_path = self.base_dir / safe
        if worktree_path.exists():
            raise WorktreeError(f"worktree path already in use: {worktree_path}")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        return worktree_path

    def _discard(self, worktree_path: Path) -> None:
        """Remove the checkout directory and the temporary parent this pool created for it."""
        shutil.rmtree(worktree_path, ignore_errors=True)
        if self.base_dir is None:
            shutil.rmtree(worktree_path.parent, ignore_errors=True)


def _run(repo: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run git in ``repo``, with its output kept out of the terminal."""
    return subprocess.run(args, cwd=repo, check=check, capture_output=True, text=True)


def _failure(what: str, target: str, error: subprocess.CalledProcessError) -> str:
    """One readable line out of a failed git command."""
    detail = (error.stderr or "").strip() or str(error)
    return f"git {what} failed for {target}: {detail}"
