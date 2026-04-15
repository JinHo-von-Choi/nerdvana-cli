"""Skill system — markdown-based prompt plugins."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from nerdvana_cli.core import paths

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    name: str
    description: str
    trigger: str
    body: str
    source: Path | None = None

    @classmethod
    def from_file(cls, path: Path) -> Skill:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            raise ValueError(f"No frontmatter in {path}")
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError(f"Invalid frontmatter in {path}")
        frontmatter = yaml.safe_load(parts[1]) or {}
        body = parts[2].strip()
        name = frontmatter.get("name", path.stem)
        description = frontmatter.get("description", "")
        trigger = frontmatter.get("trigger", f"/{name}")
        return cls(name=name, description=description, trigger=trigger, body=body, source=path)


class SkillLoader:
    def __init__(self, project_dir: str = ".", global_dir: str | None = None):
        self._project_dir = Path(project_dir)
        self._global_dir  = Path(global_dir) if global_dir else paths.user_skills_dir()
        self._skills: list[Skill] = []

    def load_all(self) -> list[Skill]:
        skills_by_name: dict[str, Skill] = {}
        builtin_dir = Path(__file__).parent.parent / "skills"
        for skill in self._load_dir(builtin_dir):
            skills_by_name[skill.name] = skill
        for skill in self._load_dir(self._global_dir):
            skills_by_name[skill.name] = skill
        project_skills_dir = self._project_dir / ".nerdvana" / "skills"
        for skill in self._load_dir(project_skills_dir):
            skills_by_name[skill.name] = skill
        self._skills = list(skills_by_name.values())
        return self._skills

    def get_by_trigger(self, trigger: str) -> Skill | None:
        for skill in self._skills:
            if skill.trigger == trigger:
                return skill
        return None

    def get_by_name(self, name: str) -> Skill | None:
        for skill in self._skills:
            if skill.name == name:
                return skill
        return None

    def list_skills(self) -> list[Skill]:
        return self._skills

    def _load_dir(self, directory: Path) -> list[Skill]:
        skills: list[Skill] = []
        if not directory.exists():
            return skills
        for path in sorted(directory.glob("*.md")):
            try:
                skill = Skill.from_file(path)
                skills.append(skill)
            except Exception as e:
                logger.warning("Failed to load skill %s: %s", path, e)
        return skills
