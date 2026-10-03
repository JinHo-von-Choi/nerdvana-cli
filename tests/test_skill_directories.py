"""Directory skills (<name>/SKILL.md), loader limits, and optional .claude/skills tiers."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.config.settings_sections import SkillsConfig
from nerdvana_cli.core.context.skills import MAX_SKILL_BYTES, SkillLoader


def _write_skill(path: Path, name: str, body: str = "Body") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: D\n---\n\n{body}\n")


def _loader(tmp_path: Path, **kwargs: Any) -> SkillLoader:
    return SkillLoader(
        project_dir=str(tmp_path / "proj"),
        global_dir=str(tmp_path / "global"),
        claude_global_dir=str(tmp_path / "claude_global"),
        **kwargs,
    )


def _project_skills(tmp_path: Path) -> Path:
    return tmp_path / "proj" / ".nerdvana" / "skills"


def test_directory_skill_loads(tmp_path: Path) -> None:
    base = _project_skills(tmp_path)
    _write_skill(base / "deploy" / "SKILL.md", "deploy", "Dir body")
    (base / "deploy" / "run.py").write_text("raise SystemExit(1)")
    deploy = next(s for s in _loader(tmp_path).load_all() if s.name == "deploy")
    assert deploy.body == "Dir body"
    assert deploy.trigger == "/deploy"


def test_directory_skill_name_defaults_to_dirname(tmp_path: Path) -> None:
    f = _project_skills(tmp_path) / "ship" / "SKILL.md"
    f.parent.mkdir(parents=True)
    f.write_text("---\ndescription: D\n---\n\nBody\n")
    skills = _loader(tmp_path).load_all()
    assert any(s.name == "ship" and s.trigger == "/ship" for s in skills)


def test_single_file_skill_still_loads(tmp_path: Path) -> None:
    _write_skill(_project_skills(tmp_path) / "solo.md", "solo")
    assert any(s.name == "solo" for s in _loader(tmp_path).load_all())


def test_same_tier_collision_directory_wins_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    base = _project_skills(tmp_path)
    _write_skill(base / "dup.md", "dup", "file body")
    _write_skill(base / "dup" / "SKILL.md", "dup", "dir body")
    with caplog.at_level(logging.WARNING, logger="nerdvana_cli.core.context.skills"):
        skills = _loader(tmp_path).load_all()
    dup = [s for s in skills if s.name == "dup"]
    assert len(dup) == 1
    assert dup[0].body == "dir body"
    assert any("dup" in r.getMessage() for r in caplog.records)


def test_cross_tier_precedence_applies_to_directory_skills(tmp_path: Path) -> None:
    _write_skill(tmp_path / "global" / "x" / "SKILL.md", "x", "global dir")
    _write_skill(_project_skills(tmp_path) / "x.md", "x", "project file")
    x = next(s for s in _loader(tmp_path).load_all() if s.name == "x")
    assert x.body == "project file"


def test_symlink_outside_tier_skipped(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _write_skill(outside / "evil" / "SKILL.md", "evil")
    _write_skill(outside / "evilfile.md", "evilfile")
    base = _project_skills(tmp_path)
    base.mkdir(parents=True)
    (base / "evil").symlink_to(outside / "evil", target_is_directory=True)
    (base / "evilfile.md").symlink_to(outside / "evilfile.md")
    names = {s.name for s in _loader(tmp_path).load_all()}
    assert "evil" not in names
    assert "evilfile" not in names


def test_symlink_inside_tier_allowed(tmp_path: Path) -> None:
    base = _project_skills(tmp_path)
    _write_skill(base / "_real" / "SKILL.md", "linked")
    (base / "alias").symlink_to(base / "_real", target_is_directory=True)
    assert any(s.name == "linked" for s in _loader(tmp_path).load_all())


def test_oversize_skill_skipped(tmp_path: Path) -> None:
    base = _project_skills(tmp_path)
    _write_skill(base / "big" / "SKILL.md", "big", "x" * (MAX_SKILL_BYTES + 1))
    _write_skill(base / "bigfile.md", "bigfile", "x" * (MAX_SKILL_BYTES + 1))
    _write_skill(base / "small" / "SKILL.md", "small")
    names = {s.name for s in _loader(tmp_path).load_all()}
    assert "big" not in names
    assert "bigfile" not in names
    assert "small" in names


def test_claude_skills_ignored_by_default(tmp_path: Path) -> None:
    _write_skill(tmp_path / "claude_global" / "g" / "SKILL.md", "cg")
    _write_skill(tmp_path / "proj" / ".claude" / "skills" / "p" / "SKILL.md", "cp")
    names = {s.name for s in _loader(tmp_path).load_all()}
    assert "cg" not in names
    assert "cp" not in names


def test_claude_skills_loaded_when_enabled(tmp_path: Path) -> None:
    _write_skill(tmp_path / "claude_global" / "g" / "SKILL.md", "cg")
    _write_skill(tmp_path / "proj" / ".claude" / "skills" / "p" / "SKILL.md", "cp")
    names = {s.name for s in _loader(tmp_path, include_claude_skills=True).load_all()}
    assert {"cg", "cp"} <= names


def test_claude_tiers_sit_below_matching_nerdvana_tiers(tmp_path: Path) -> None:
    _write_skill(tmp_path / "claude_global" / "a.md", "a", "claude global")
    _write_skill(tmp_path / "global" / "a.md", "a", "nerdvana global")
    _write_skill(tmp_path / "proj" / ".claude" / "skills" / "b.md", "b", "claude project")
    _write_skill(_project_skills(tmp_path) / "b.md", "b", "nerdvana project")
    _write_skill(tmp_path / "proj" / ".claude" / "skills" / "c.md", "c", "claude project")
    _write_skill(tmp_path / "global" / "c.md", "c", "nerdvana global")
    skills = {s.name: s for s in _loader(tmp_path, include_claude_skills=True).load_all()}
    assert skills["a"].body == "nerdvana global"
    assert skills["b"].body == "nerdvana project"
    assert skills["c"].body == "claude project"


def test_skills_setting_defaults_off() -> None:
    assert SkillsConfig().include_claude_skills is False
    assert NerdvanaSettings(_env_file=None).skills.include_claude_skills is False  # type: ignore[call-arg]
