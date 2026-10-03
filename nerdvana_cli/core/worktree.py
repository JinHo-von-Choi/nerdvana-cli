"""A separate working copy for a sub-agent whose changes should not land in the project directly.

Author: 최진호
Date:   2026-10-03

``git worktree`` gives the agent its own checkout of the current commit on a branch of its own, in a temporary
directory. Its edits do not touch the project directory, whatever they are, and nothing needs merging until a
person decides: when the agent is done the worktree is removed if it changed nothing, and kept, with its path
and branch, if it did. Uncommitted changes of the project are not in the copy: it starts from ``HEAD``.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path


class WorktreeError(RuntimeError):
    """A worktree could not be created or inspected."""


@dataclass(frozen=True)
class Worktree:
    """A checkout made for one agent."""

    root:   str   # the repository it belongs to
    path:   str
    branch: str
    start:  str   # the commit it was made from


def _git(root: str, *args: str) -> str:
    done = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=False)  # noqa: S603, S607
    if done.returncode:
        raise WorktreeError(done.stderr.strip() or f"git {args[0]} failed")
    return done.stdout


def create_worktree(project: str, label: str) -> Worktree:
    """A new worktree of the repository containing *project*, on a new branch named after *label*."""
    try:
        top = _git(project, "rev-parse", "--show-toplevel").strip()
        start = _git(top, "rev-parse", "--verify", "HEAD").strip()
    except WorktreeError as exc:
        raise WorktreeError(f"worktree isolation needs a git repository with a commit ({exc})") from exc
    slug   = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-")[:30] or "agent"
    branch = f"nerdvana/{slug}-{uuid.uuid4().hex[:6]}"
    path   = str(Path(tempfile.mkdtemp(prefix="nerdvana-worktree-")) / "work")
    _git(top, "worktree", "add", "-q", "-b", branch, path, "HEAD")
    return Worktree(top, path, branch, start)


def has_changes(worktree: Worktree) -> bool:
    """True when the worktree holds uncommitted changes or has moved past the commit it started from."""
    return bool(_git(worktree.path, "status", "--porcelain").strip()) or _git(worktree.path, "rev-parse", "HEAD").strip() != worktree.start


def git_dirs(worktree: Worktree) -> list[str]:
    """The git directories a commit in the worktree writes to: its own and the shared one (objects, refs).

    A sandbox that confines commands to the worktree has to allow these, or ``git commit`` cannot work there.
    """
    own    = _git(worktree.path, "rev-parse", "--absolute-git-dir").strip()
    common = str((Path(worktree.path) / _git(worktree.path, "rev-parse", "--git-common-dir").strip()).resolve())
    return [own] if own == common else [own, common]


def remove_worktree(worktree: Worktree) -> None:
    """Delete the worktree and its branch."""
    _git(worktree.root, "worktree", "remove", "--force", worktree.path)
    _git(worktree.root, "branch", "-D", worktree.branch)
