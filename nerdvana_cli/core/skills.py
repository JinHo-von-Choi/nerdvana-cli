"""Skill system: markdown-based prompt plugins.

A skill is either a single ``<name>.md`` file or a ``<name>/SKILL.md``
directory. Only the SKILL.md text is ever read; other files bundled in a
skill directory are never loaded or executed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped,unused-ignore]

from nerdvana_cli.core import paths

logger = logging.getLogger(__name__)

SKILL_DIR_FILENAME = "SKILL.md"
MAX_SKILL_BYTES    = 64 * 1024


@dataclass
class Skill:
    name: str
    description: str
    trigger: str
    body: str
    source: Path | None = None

    @classmethod
    def from_file(cls, path: Path, default_name: str | None = None) -> Skill:
        """Parse a skill file; ``default_name`` replaces the file stem when frontmatter has no name."""
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            raise ValueError(f"No frontmatter in {path}")
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError(f"Invalid frontmatter in {path}")
        frontmatter = yaml.safe_load(parts[1]) or {}
        body = parts[2].strip()
        name = frontmatter.get("name", default_name or path.stem)
        description = frontmatter.get("description", "")
        trigger = frontmatter.get("trigger", f"/{name}")
        return cls(name=name, description=description, trigger=trigger, body=body, source=path)


class SkillLoader:
    def __init__(
        self,
        project_dir: str = ".",
        global_dir: str | None = None,
        include_claude_skills: bool = False,
        claude_global_dir: str | None = None,
    ):
        self._project_dir           = Path(project_dir)
        self._global_dir            = Path(global_dir) if global_dir else paths.user_skills_dir()
        self._include_claude_skills = include_claude_skills
        self._claude_global_dir     = (
            Path(claude_global_dir) if claude_global_dir else Path.home() / ".claude" / "skills"
        )
        self._skills: list[Skill] = []

    def _tiers(self) -> list[Path]:
        """Skill directories in ascending precedence (later tiers override earlier ones)."""
        tiers: list[Path] = [Path(__file__).parent.parent / "skills"]
        if self._include_claude_skills:
            tiers.append(self._claude_global_dir)
        tiers.append(self._global_dir)
        if self._include_claude_skills:
            tiers.append(self._project_dir / ".claude" / "skills")
        tiers.append(self._project_dir / ".nerdvana" / "skills")
        return tiers

    def load_all(self) -> list[Skill]:
        skills_by_name: dict[str, Skill] = {}
        for tier in self._tiers():
            for skill in self._load_dir(tier):
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
        """Load one tier: single-file skills first, then directory skills (which win on a name clash)."""
        if not directory.is_dir():
            return []
        root = directory.resolve()
        by_name: dict[str, Skill] = {}
        for path in sorted(directory.glob("*.md")):
            skill = self._load_skill(path, root, default_name=None)
            if skill is not None:
                by_name[skill.name] = skill
        for entry in sorted(directory.iterdir()):
            skill_file = entry / SKILL_DIR_FILENAME
            if not entry.is_dir() or not skill_file.is_file():
                continue
            skill = self._load_skill(skill_file, root, default_name=entry.name)
            if skill is None:
                continue
            clash = by_name.get(skill.name)
            if clash is not None:
                logger.warning(
                    "Skill %r defined as both %s and %s; using the directory skill",
                    skill.name, clash.source, skill_file,
                )
            by_name[skill.name] = skill
        return list(by_name.values())

    def _load_skill(self, path: Path, root: Path, default_name: str | None) -> Skill | None:
        """Parse one skill file, or return None when it escapes the tier, is oversize, or is invalid."""
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                logger.warning("Skipping skill %s: resolves outside %s", path, root)
                return None
            size = resolved.stat().st_size
            if size > MAX_SKILL_BYTES:
                logger.warning("Skipping skill %s: %d bytes exceeds %d", path, size, MAX_SKILL_BYTES)
                return None
            return Skill.from_file(path, default_name=default_name)
        except Exception as e:
            logger.warning("Failed to load skill %s: %s", path, e)
            return None
