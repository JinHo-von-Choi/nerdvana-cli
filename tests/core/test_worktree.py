"""A sub-agent in its own git worktree: creation, change detection, cleanup and the Agent tool around it.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.delegation.task_state import TaskRegistry
from nerdvana_cli.core.delegation.worktree import WorktreeError, create_worktree, git_dirs, has_changes, remove_worktree
from nerdvana_cli.core.loop.subagent_config import SubagentConfig
from nerdvana_cli.core.safety import sandbox
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.agent_tool import AgentTool, AgentToolArgs


def _git(root: Path | str, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True, text=True)
    return done.stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


def test_a_worktree_is_a_separate_checkout_of_head_on_its_own_branch(repo: Path) -> None:
    worktree = create_worktree(str(repo), "Fix the parser!")
    try:
        assert Path(worktree.path).is_dir() and Path(worktree.path) != repo
        assert (Path(worktree.path) / "a.txt").read_text() == "one\n"
        assert worktree.branch.startswith("nerdvana/Fix-the-parser-") and _git(worktree.path, "branch", "--show-current").strip() == worktree.branch
        assert not has_changes(worktree)
    finally:
        remove_worktree(worktree)


def test_edits_in_it_do_not_touch_the_project_directory(repo: Path) -> None:
    worktree = create_worktree(str(repo), "x")
    try:
        (Path(worktree.path) / "a.txt").write_text("changed\n")
        assert (repo / "a.txt").read_text() == "one\n" and has_changes(worktree)
    finally:
        remove_worktree(worktree)


def test_uncommitted_and_committed_changes_are_both_changes(repo: Path) -> None:
    worktree = create_worktree(str(repo), "x")
    try:
        (Path(worktree.path) / "new.txt").write_text("n\n")
        assert has_changes(worktree)
        _git(worktree.path, "add", "-A")
        _git(worktree.path, "commit", "-q", "-m", "work")
        assert has_changes(worktree)                       # clean tree, but it moved past its start
    finally:
        remove_worktree(worktree)


def test_removing_deletes_the_directory_and_the_branch(repo: Path) -> None:
    worktree = create_worktree(str(repo), "x")
    remove_worktree(worktree)
    assert not Path(worktree.path).exists() and worktree.branch not in _git(repo, "branch", "--list")


def test_a_directory_that_is_not_a_repository_or_has_no_commit_is_refused(tmp_path: Path) -> None:
    with pytest.raises(WorktreeError, match="needs a git repository"):
        create_worktree(str(tmp_path), "x")
    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "-q")
    with pytest.raises(WorktreeError, match="with a commit"):
        create_worktree(str(empty), "x")


def test_the_git_directories_include_the_shared_one_where_commits_write(repo: Path) -> None:
    worktree = create_worktree(str(repo), "x")
    try:
        dirs = git_dirs(worktree)
        assert str((repo / ".git").resolve()) in dirs and len(dirs) == 2
    finally:
        remove_worktree(worktree)


# ---------------------------------------------------------------------------
# Through the Agent tool
# ---------------------------------------------------------------------------


async def _call(repo: Path, isolation: str, work: object = None) -> tuple[str, SubagentConfig | None]:
    seen: list[SubagentConfig] = []

    async def fake(config: SubagentConfig, abort: object) -> tuple[str, int]:
        seen.append(config)
        if work is not None:
            (Path(config.settings.cwd) / "made.txt").write_text("x")
        return "agent output", 1

    registry = TaskRegistry()
    with patch("nerdvana_cli.tools.agent_tool.run_subagent", new=AsyncMock(side_effect=fake)):
        result = await AgentTool(settings=NerdvanaSettings(), task_registry=registry).call(
            AgentToolArgs(prompt="p", isolation=isolation, description="try it"), ToolContext(cwd=str(repo), task_registry=registry), can_use_tool=None,
        )
    return result.content, (seen[0] if seen else None)


async def test_an_isolated_agent_runs_in_the_worktree_and_leaves_changes_there(repo: Path) -> None:
    content, config = await _call(repo, "worktree", work=True)
    assert config is not None and config.settings.cwd != str(repo)
    assert "its own git worktree" in content and "branch nerdvana/try-it-" in content
    assert not (repo / "made.txt").exists() and (Path(config.settings.cwd) / "made.txt").exists()
    _git(repo, "worktree", "remove", "--force", config.settings.cwd)


async def test_an_isolated_agent_that_changed_nothing_has_its_worktree_removed(repo: Path) -> None:
    content, config = await _call(repo, "worktree")
    assert "had no changes and was removed" in content
    assert config is not None and not Path(config.settings.cwd).exists()
    assert "nerdvana/" not in _git(repo, "branch", "--list")


async def test_without_isolation_nothing_changes(repo: Path) -> None:
    content, config = await _call(repo, "")
    assert content == "agent output" and config is not None and config.settings.cwd != "/nonexistent"
    assert _git(repo, "worktree", "list").count("\n") == 1


async def test_isolation_outside_a_repository_is_an_error_result(tmp_path: Path) -> None:
    content, config = await _call(tmp_path, "worktree")
    assert "needs a git repository" in content and config is None


@pytest.mark.skipif(sandbox.landlock_abi() < 1, reason="the kernel has no Landlock")
async def test_a_sandboxed_agent_can_commit_in_its_worktree(repo: Path) -> None:
    from nerdvana_cli.core.safety.sandbox import SandboxPolicy
    from nerdvana_cli.tools.agent_tool import enter_worktree
    from nerdvana_cli.tools.bash_tool import BashArgs, BashTool

    settings = NerdvanaSettings()
    settings.sandbox.mode = "require"
    worktree = enter_worktree(AgentToolArgs(prompt="p", isolation="worktree"), "t1", settings, ToolContext(cwd=str(repo)))
    assert worktree is not None
    try:
        context = ToolContext(cwd=worktree.path)
        context.state["sandbox"] = SandboxPolicy("require", True, tuple(settings.sandbox.write_paths))
        result = await BashTool().call(BashArgs("echo hi > new.txt && git add new.txt && git -c user.name=t -c user.email=t@t commit -q -m work && echo committed"), context)
        assert "committed" in result.content, result.content
        elsewhere = Path(__file__).parent / f".worktree-outside-{repo.parent.name}"   # /tmp itself is writable to commands
        elsewhere.mkdir()
        try:
            outside = await BashTool().call(BashArgs(f"echo x > {elsewhere}/leak.txt; echo status=$?"), context)
            assert not (elsewhere / "leak.txt").exists() and "Permission denied" in outside.content
        finally:
            elsewhere.rmdir()
    finally:
        remove_worktree(worktree)
