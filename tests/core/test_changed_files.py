"""A Bash command's output names the files it changed in a git working tree, when asked to.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nerdvana_cli.core.changed_files import changed, report, snapshot
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.bash_tool import BashArgs, BashTool


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("one\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_changed_compares_new_gone_and_different_entries() -> None:
    assert changed({"a": "1", "b": "2"}, {"a": "1", "b": "3", "c": "4"}) == ["b", "c"]
    assert changed({"a": "1"}, {}) == ["a"]


def test_report_lists_a_few_and_counts_the_rest() -> None:
    assert report([]) == ""
    assert report(["a", "b"]) == "\n[Files changed by this command: a, b]"
    assert report([f"f{i}" for i in range(13)]).endswith("f9 (+3 more)]")


async def test_a_snapshot_is_none_outside_a_git_tree(tmp_path: Path) -> None:
    assert await snapshot(str(tmp_path)) is None


async def test_the_bash_tool_names_what_a_command_changed_only_when_enabled(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    command = "echo two >> tracked.txt; echo new > created.txt; echo hi"

    plain = await BashTool().call(BashArgs(command), ToolContext(cwd=str(repo)))
    assert "Files changed" not in plain.content

    (repo / "tracked.txt").write_text("one\n")
    (repo / "created.txt").unlink()
    context = ToolContext(cwd=str(repo))
    context.state["report_bash_changes"] = True
    reported = await BashTool().call(BashArgs(command), context)
    assert "[Files changed by this command: created.txt, tracked.txt]" in reported.content


async def test_a_command_that_changes_nothing_adds_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    context = ToolContext(cwd=str(repo))
    context.state["report_bash_changes"] = True
    result = await BashTool().call(BashArgs("echo quiet"), context)
    assert "Files changed" not in result.content


def test_the_setting_is_off_by_default() -> None:
    from nerdvana_cli.core.config.settings import NerdvanaSettings

    assert NerdvanaSettings().session.report_bash_changes is False


@pytest.mark.parametrize("name", ["report_bash_changes"])
def test_the_loop_passes_the_flag_to_tools(name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from nerdvana_cli.core.agent_loop import AgentLoop
    from nerdvana_cli.core.config.settings import NerdvanaSettings
    from nerdvana_cli.core.state.session import SessionStorage
    from nerdvana_cli.core.tool import ToolRegistry

    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(AgentLoop, "create_provider_from_settings", lambda self: None)
    settings = NerdvanaSettings()
    settings.cwd = str(tmp_path)
    settings.session.report_bash_changes = True
    loop = AgentLoop(settings=settings, registry=ToolRegistry(), session=SessionStorage(session_id="c", storage_dir=str(tmp_path / "s")))
    assert loop._new_tool_context().state[name] is True
