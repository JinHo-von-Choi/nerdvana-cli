"""Tests for nerdvana_cli.commands.memory_command.

Author: 최진호
Date:   2026-04-29
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(args: list[str], data_home: str) -> object:
    from nerdvana_cli.main import app
    runner = CliRunner()
    env = {"NERDVANA_DATA_HOME": data_home}
    return runner.invoke(app, args, env=env, catch_exceptions=False)


def _run_in(args: list[str], data_home: str, cwd: Path) -> object:
    """Run CLI with nerdvana_cli.commands.memory_command._cwd patched to cwd."""
    from unittest.mock import patch

    from nerdvana_cli.main import app
    runner = CliRunner()
    env    = {"NERDVANA_DATA_HOME": data_home}
    with patch("nerdvana_cli.commands.memory_command._cwd", return_value=str(cwd)):
        return runner.invoke(app, args, env=env, catch_exceptions=False)


# ---------------------------------------------------------------------------
# Unit tests — MemoriesManager via memory_command helpers
# ---------------------------------------------------------------------------

class TestResolveScope:
    def test_project(self) -> None:
        from nerdvana_cli.commands.memory_command import _resolve_scope
        from nerdvana_cli.core.memories import MemoryScope
        assert _resolve_scope("project") == MemoryScope.PROJECT_KNOWLEDGE

    def test_global(self) -> None:
        from nerdvana_cli.commands.memory_command import _resolve_scope
        from nerdvana_cli.core.memories import MemoryScope
        assert _resolve_scope("global") == MemoryScope.USER_GLOBAL

    def test_rule(self) -> None:
        from nerdvana_cli.commands.memory_command import _resolve_scope
        from nerdvana_cli.core.memories import MemoryScope
        assert _resolve_scope("rule") == MemoryScope.PROJECT_RULE

    def test_invalid_raises(self) -> None:
        import typer

        from nerdvana_cli.commands.memory_command import _resolve_scope
        with pytest.raises(typer.BadParameter):
            _resolve_scope("unknown_scope")


# ---------------------------------------------------------------------------
# CLI integration — memory add + list
# ---------------------------------------------------------------------------

class TestMemoryAdd:
    def test_add_project_memory(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        result = _run_in(
            ["memory", "add", "hello world", "--name", "greeting", "--scope", "project"],
            str(tmp_path),
            project_dir,
        )
        assert result.exit_code == 0
        mem_file = project_dir / ".nerdvana" / "memories" / "greeting.md"
        assert mem_file.exists()
        assert "hello world" in mem_file.read_text(encoding="utf-8")

    def test_add_global_memory(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        result = _run_in(
            ["memory", "add", "global fact", "--name", "gfact", "--scope", "global"],
            str(tmp_path),
            project_dir,
        )
        assert result.exit_code == 0
        global_file = tmp_path / "memories" / "global" / "gfact.md"
        assert global_file.exists()

    def test_add_without_name_generates_key(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        result = _run_in(
            ["memory", "add", "no name given", "--scope", "project"],
            str(tmp_path),
            project_dir,
        )
        assert result.exit_code == 0
        mem_dir = project_dir / ".nerdvana" / "memories"
        assert any(mem_dir.rglob("*.md"))


class TestMemoryList:
    def test_list_empty(self, tmp_path: Path) -> None:
        result = _run(["memory", "list", "--scope", "project"], str(tmp_path))
        assert result.exit_code == 0
        assert "No memories" in result.output

    def test_list_shows_added_memory(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _run_in(
            ["memory", "add", "content", "--name", "listed", "--scope", "project"],
            str(tmp_path),
            project_dir,
        )
        result = _run_in(
            ["memory", "list", "--scope", "project"],
            str(tmp_path),
            project_dir,
        )
        assert result.exit_code == 0
        assert "listed" in result.output


# ---------------------------------------------------------------------------
# CLI integration — memory remove
# ---------------------------------------------------------------------------

class TestMemoryRemove:
    def test_remove_existing(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _run_in(
            ["memory", "add", "to delete", "--name", "del-me", "--scope", "project"],
            str(tmp_path),
            project_dir,
        )
        result = _run_in(
            ["memory", "remove", "del-me"],
            str(tmp_path),
            project_dir,
        )
        assert result.exit_code == 0
        assert not (project_dir / ".nerdvana" / "memories" / "del-me.md").exists()

    def test_remove_missing_exits_nonzero(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from nerdvana_cli.main import app
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        runner = CliRunner()
        env = {"NERDVANA_DATA_HOME": str(tmp_path)}
        with patch("nerdvana_cli.commands.memory_command._cwd", return_value=str(project_dir)):
            result = runner.invoke(app, ["memory", "remove", "nonexistent"], env=env)
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# CLI integration — memory purge
# ---------------------------------------------------------------------------

class TestMemoryPurge:
    def test_purge_project_scope(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        for i in range(3):
            _run_in(
                ["memory", "add", f"entry {i}", "--name", f"mem{i}", "--scope", "project"],
                str(tmp_path),
                project_dir,
            )
        result = _run_in(
            ["memory", "purge", "--scope", "project"],
            str(tmp_path),
            project_dir,
        )
        assert result.exit_code == 0
        mem_dir = project_dir / ".nerdvana" / "memories"
        assert list(mem_dir.rglob("*.md")) == []

    def test_purge_empty_scope_no_crash(self, tmp_path: Path) -> None:
        result = _run(["memory", "purge", "--scope", "global"], str(tmp_path))
        assert result.exit_code == 0

    def test_purge_rule_scope_exits_nonzero(self, tmp_path: Path) -> None:
        result = _run(["memory", "purge", "--scope", "rule"], str(tmp_path))
        assert result.exit_code != 0
