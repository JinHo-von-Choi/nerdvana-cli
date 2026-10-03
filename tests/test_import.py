"""nerdvana import: commands and permission rules from Claude Code and Codex.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nerdvana_cli.core.importer import apply_commands, convert_rule, plan_import, read_permissions
from nerdvana_cli.core.user_commands import UserCommandLoader
from nerdvana_cli.main import app


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    home, project = tmp_path / "home", tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home, project


@pytest.mark.parametrize(("rule", "expected"), [
    ("Bash(git diff:*)", "Bash(git diff *)"),
    ("Bash(npm test)", "Bash(npm test)"),
    ("Read", "Read"),
    ("  WebFetch(domain:example.com)  ", "WebFetch(domain:example.com)"),
])
def test_prefix_rules_become_globs_and_the_rest_is_kept(rule: str, expected: str) -> None:
    assert convert_rule(rule) == expected


def test_permissions_are_read_from_a_settings_file_and_a_broken_one_gives_nothing(tmp_path: Path) -> None:
    ok = _write(tmp_path / "s.json", json.dumps({"permissions": {"allow": ["Bash(ls:*)", "Read"], "deny": ["Bash(rm:*)", 5]}}))
    assert read_permissions(ok) == (["Bash(ls *)", "Read"], ["Bash(rm *)"])
    assert read_permissions(_write(tmp_path / "bad.json", "{nope")) == ([], [])
    assert read_permissions(tmp_path / "absent.json") == ([], [])
    assert read_permissions(_write(tmp_path / "other.json", '{"permissions": 3}')) == ([], [])


def test_claude_commands_and_rules_are_found_in_the_project_and_the_home(world: tuple[Path, Path]) -> None:
    home, project = world
    _write(home / ".claude" / "commands" / "review.md", "Review $ARGUMENTS")
    _write(project / ".claude" / "commands" / "git" / "commit.md", "Write a commit message")
    _write(project / ".claude" / "settings.json", json.dumps({"permissions": {"allow": ["Bash(git status)"]}}))
    _write(project / "CLAUDE.md", "rules")
    plan = plan_import("claude", project, home)
    assert sorted(dst.relative_to(project).as_posix() for _, dst in plan.commands) == [".nerdvana/commands/git/commit.md", ".nerdvana/commands/review.md"]
    assert plan.allow == ["Bash(git status)"] and any("CLAUDE.md" in n for n in plan.notes)


def test_codex_prompts_are_commands_and_carry_no_rules(world: tuple[Path, Path]) -> None:
    home, project = world
    _write(home / ".codex" / "prompts" / "plan.md", "Plan it")
    plan = plan_import("codex", project, home)
    assert [dst.name for _, dst in plan.commands] == ["plan.md"] and plan.allow == [] and plan.deny == []


def test_an_existing_command_and_a_duplicate_name_are_skipped_and_never_overwritten(world: tuple[Path, Path]) -> None:
    home, project = world
    _write(home / ".claude" / "commands" / "fix.md", "from home")
    _write(project / ".claude" / "commands" / "fix.md", "from project")
    _write(project / ".nerdvana" / "commands" / "keep.md", "mine")
    _write(home / ".claude" / "commands" / "keep.md", "theirs")
    plan = plan_import("claude", project, home)
    assert len(plan.commands) == 1 and {why for _, why in plan.skipped} >= {"a command with this name exists here already"}
    assert apply_commands(plan) == 1
    assert (project / ".nerdvana" / "commands" / "keep.md").read_text() == "mine"


def test_odd_file_names_are_skipped(world: tuple[Path, Path]) -> None:
    home, project = world
    _write(home / ".claude" / "commands" / "bad name.md", "x")
    plan = plan_import("claude", project, home)
    assert plan.commands == [] and "not a usable command name" in plan.skipped[0][1]


def test_what_was_copied_is_found_by_the_command_loader(world: tuple[Path, Path]) -> None:
    home, project = world
    _write(project / ".claude" / "commands" / "git" / "commit.md", "Write a commit message")
    apply_commands(plan_import("claude", project, home))
    loader = UserCommandLoader(str(project), str(home / ".nerdvana" / "commands"))
    assert loader.get("git:commit") is not None


def test_the_command_shows_a_plan_by_default_and_writes_with_the_flag(world: tuple[Path, Path]) -> None:
    home, project = world
    _write(project / ".claude" / "commands" / "go.md", "Go")
    _write(project / ".claude" / "settings.json", json.dumps({"permissions": {"allow": ["Bash(make:*)"], "deny": ["Bash(rm:*)"]}}))
    runner = CliRunner()
    shown  = runner.invoke(app, ["import", "claude", "--project", str(project)])
    assert shown.exit_code == 0 and "would copy 1" in shown.stdout and "Nothing was written" in shown.stdout
    assert 'always_allow:\n    - "Bash(make *)"' in shown.stdout and 'always_deny:\n    - "Bash(rm *)"' in shown.stdout
    assert not (project / ".nerdvana" / "commands" / "go.md").exists()
    done = runner.invoke(app, ["import", "claude", "--project", str(project), "--write"])
    assert "copied 1" in done.stdout and (project / ".nerdvana" / "commands" / "go.md").read_text() == "Go"


def test_an_unknown_source_is_refused() -> None:
    assert CliRunner().invoke(app, ["import", "cursor"]).exit_code == 2
