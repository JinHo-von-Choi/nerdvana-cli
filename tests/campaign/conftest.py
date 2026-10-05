"""Repositories for the campaign tests: one git repository per fixture, ready to be checked out.

작성자: 최진호
날짜: 2026-10-05
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> str:
    """git against ``repo`` as a throwaway tester, with its output kept out of the terminal."""
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def _new_repo(path: Path) -> Path:
    """A repository with one committed file, on a branch named main."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, capture_output=True)
    (path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "initial")
    return path


@pytest.fixture
def git() -> Callable[..., str]:
    """Run git inside a repository of the test's choosing."""
    return _git


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """One repository for a campaign task to work on."""
    return _new_repo(tmp_path / "origin")


@pytest.fixture
def git_repos(tmp_path: Path) -> dict[str, Path]:
    """Three repositories: a campaign with a task per repository."""
    return {name: _new_repo(tmp_path / name) for name in ("alpha", "beta", "gamma")}
