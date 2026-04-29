"""Tests for nerdvana_cli.commands.skill_command.

Author: 최진호
Date:   2026-04-29
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SKILL_MD = """\
---
name: testskill
description: A test skill
trigger: /testskill
---
This is the skill body.
"""


def _run(args: list[str], data_home: str) -> object:
    from nerdvana_cli.main import app
    runner = CliRunner()
    return runner.invoke(app, args, env={"NERDVANA_DATA_HOME": data_home})


def _make_skill_file(skills_dir: Path, name: str, content: str = _SKILL_MD) -> Path:
    skills_dir.mkdir(parents=True, exist_ok=True)
    path = skills_dir / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI integration — skill list
# ---------------------------------------------------------------------------

class TestSkillList:
    def test_empty_dir(self, tmp_path: Path) -> None:
        result = _run(["skill", "list"], str(tmp_path))
        assert result.exit_code == 0
        assert "No skills" in result.output

    def test_shows_skill(self, tmp_path: Path) -> None:
        _make_skill_file(tmp_path / "skills", "myskill")
        result = _run(["skill", "list"], str(tmp_path))
        assert result.exit_code == 0
        assert "myskill" in result.output

    def test_shows_directory_skill(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "skills" / "dirskill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
        result = _run(["skill", "list"], str(tmp_path))
        assert result.exit_code == 0
        assert "dirskill" in result.output


# ---------------------------------------------------------------------------
# CLI integration — skill show
# ---------------------------------------------------------------------------

class TestSkillShow:
    def test_show_existing(self, tmp_path: Path) -> None:
        _make_skill_file(tmp_path / "skills", "showme", content="skill body here")
        result = _run(["skill", "show", "showme"], str(tmp_path))
        assert result.exit_code == 0
        assert "skill body here" in result.output

    def test_show_directory_skill(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "skills" / "dirshow"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("dir skill content", encoding="utf-8")
        result = _run(["skill", "show", "dirshow"], str(tmp_path))
        assert result.exit_code == 0
        assert "dir skill content" in result.output

    def test_show_missing_exits_nonzero(self, tmp_path: Path) -> None:
        result = _run(["skill", "show", "nosuchskill"], str(tmp_path))
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# CLI integration — skill install
# ---------------------------------------------------------------------------

class TestSkillInstall:
    def test_install_file(self, tmp_path: Path) -> None:
        src = tmp_path / "myskill.md"
        src.write_text(_SKILL_MD, encoding="utf-8")

        result = _run(["skill", "install", str(src)], str(tmp_path))
        assert result.exit_code == 0
        assert (tmp_path / "skills" / "myskill.md").exists()

    def test_install_directory(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src_skill"
        src_dir.mkdir()
        (src_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")

        result = _run(["skill", "install", str(src_dir)], str(tmp_path))
        assert result.exit_code == 0
        assert (tmp_path / "skills" / "src_skill" / "SKILL.md").exists()

    def test_install_duplicate_without_force_exits_nonzero(self, tmp_path: Path) -> None:
        src = tmp_path / "dupskill.md"
        src.write_text(_SKILL_MD, encoding="utf-8")
        _run(["skill", "install", str(src)], str(tmp_path))

        src2 = tmp_path / "dupskill.md"
        result = _run(["skill", "install", str(src2)], str(tmp_path))
        assert result.exit_code != 0

    def test_install_duplicate_with_force_succeeds(self, tmp_path: Path) -> None:
        src = tmp_path / "forceskill.md"
        src.write_text("v1", encoding="utf-8")
        _run(["skill", "install", str(src)], str(tmp_path))

        src.write_text("v2", encoding="utf-8")
        result = _run(["skill", "install", "--force", str(src)], str(tmp_path))
        assert result.exit_code == 0
        content = (tmp_path / "skills" / "forceskill.md").read_text(encoding="utf-8")
        assert content == "v2"


# ---------------------------------------------------------------------------
# CLI integration — skill remove
# ---------------------------------------------------------------------------

class TestSkillRemove:
    def test_remove_existing_file(self, tmp_path: Path) -> None:
        _make_skill_file(tmp_path / "skills", "todelete")
        result = _run(["skill", "remove", "todelete"], str(tmp_path))
        assert result.exit_code == 0
        assert not (tmp_path / "skills" / "todelete.md").exists()

    def test_remove_existing_directory(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "skills" / "dirskillrm"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
        result = _run(["skill", "remove", "dirskillrm"], str(tmp_path))
        assert result.exit_code == 0
        assert not skill_dir.exists()

    def test_remove_missing_exits_nonzero(self, tmp_path: Path) -> None:
        result = _run(["skill", "remove", "ghostskill"], str(tmp_path))
        assert result.exit_code != 0
