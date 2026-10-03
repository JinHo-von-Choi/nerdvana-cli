"""Skill system: Agent Skills (agentskills.io) as markdown prompt plugins.

A skill is either a single ``<name>.md`` file or a ``<name>/SKILL.md`` directory. Loading reads
only the SKILL.md text; files bundled in a skill directory are listed on activation and read by
the model with the file tools when the instructions call for them, never loaded or executed here.

Discovery tiers, lowest precedence first (a later tier replaces an earlier skill of the same name
and logs a warning):

    built-in
    ~/.claude/skills            only with skills.include_claude_skills
    ~/.agents/skills
    ~/.nerdvana/skills
    <project>/.claude/skills    only with skills.include_claude_skills
    <project>/.agents/skills
    <project>/.nerdvana/skills

User tiers sit below project tiers; inside one level the nerdvana-specific directory wins over the
generic ``.agents`` one, which wins over ``.claude``. Project tiers hold code from the repository,
so a loader built with ``from_settings`` loads a project skill only after the project is trusted
(``hooks.allow_project_hooks`` plus an approved digest, the mechanism project hooks use).

Parsing is lenient as the client guide recommends: a skill that breaks a naming rule still loads
with a warning; a missing description or unparseable YAML skips it.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped,unused-ignore]

from nerdvana_cli.core.config import paths
from nerdvana_cli.core.user_hooks import hook_digest, load_trust_record, project_hooks_enabled

logger = logging.getLogger(__name__)

SKILL_DIR_FILENAME = "SKILL.md"
MAX_SKILL_BYTES    = 64 * 1024

# Scan bounds from the client guide: skills nest at most this deep and a tier holds at most this many directories.
MAX_SCAN_DEPTH     = 4
MAX_SCAN_DIRS      = 2000
MAX_BUNDLED_FILES  = 200
SKIPPED_DIRS       = frozenset({".git", "node_modules"})

NAME_MAX_CHARS          = 64
DESCRIPTION_MAX_CHARS   = 1024
COMPATIBILITY_MAX_CHARS = 500

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_KEY_VALUE   = re.compile(r"^([A-Za-z0-9_-]+):[ \t]+(.+?)[ \t]*$")
_YAML_MARKUP = ("'", '"', "[", "{", "|", ">", "&", "*", "!", "#", "%", "@", "`")

# Decides whether the SKILL.md at a project path may be loaded.
ProjectTrust = Callable[[Path], bool]


class SkillLoadError(RuntimeError):
    """A skill that lives somewhere other than the disk could not be loaded; the message says why."""


def name_problems(name: str) -> list[str]:
    """The Agent Skills name rules *name* breaks, as readable phrases; empty when it conforms."""
    problems: list[str] = []
    if len(name) > NAME_MAX_CHARS:
        problems.append(f"is longer than {NAME_MAX_CHARS} characters")
    if any(not (ch == "-" or (ch.isalnum() and ch == ch.lower())) for ch in name):
        problems.append("may only contain lowercase letters, digits and hyphens")
    if name.startswith("-") or name.endswith("-"):
        problems.append("must not start or end with a hyphen")
    if "--" in name:
        problems.append("must not contain consecutive hyphens")
    return problems


def _scalar_text(value: Any) -> str:
    """A frontmatter scalar as stripped text; anything that is not a string or number gives an empty string."""
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def _quote_colon_values(block: str) -> str:
    """Quote top-level values that contain ``: `` so YAML written for lenient parsers reads as plain text."""
    fixed: list[str] = []
    for line in block.splitlines():
        match = _KEY_VALUE.match(line)
        if match:
            value = match.group(2)
            if (": " in value or value.endswith(":")) and not value.startswith(_YAML_MARKUP):
                escaped = value.replace("'", "''")
                line    = f"{match.group(1)}: '{escaped}'"
        fixed.append(line)
    return "\n".join(fixed)


def _parse_frontmatter(block: str, path: Path) -> dict[str, Any]:
    """Parse the YAML block, retrying once with unquoted colon values quoted."""
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        try:
            data = yaml.safe_load(_quote_colon_values(block))
        except yaml.YAMLError as exc:
            raise ValueError(f"Unparseable frontmatter in {path}: {exc}") from exc
        logger.warning("Skill %s: frontmatter was not valid YAML; read it with colon values quoted", path)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Frontmatter in {path} is not a mapping")
    return data


def _string_map(value: Any) -> dict[str, str]:
    """The ``metadata`` field: a mapping whose keys and values are kept as strings."""
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _tool_list(value: Any) -> tuple[str, ...]:
    """The ``allowed-tools`` field: a space-separated string, or a YAML list of strings."""
    if isinstance(value, str):
        return tuple(value.split())
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()


def _warn_nonconforming(path: Path, name: str, directory_name: str | None, description: str, compatibility: str) -> None:
    """Log every way the skill departs from the specification; the skill still loads."""
    for problem in name_problems(name):
        logger.warning("Skill %s: name %r %s", path, name, problem)
    if directory_name is not None and name != directory_name:
        logger.warning("Skill %s: name %r does not match its directory %r", path, name, directory_name)
    if len(description) > DESCRIPTION_MAX_CHARS:
        logger.warning("Skill %s: description is longer than %d characters", path, DESCRIPTION_MAX_CHARS)
    if len(compatibility) > COMPATIBILITY_MAX_CHARS:
        logger.warning("Skill %s: compatibility is longer than %d characters", path, COMPATIBILITY_MAX_CHARS)


@dataclass
class Skill:
    name: str
    description: str
    trigger: str
    body: str
    source: Path | None = None
    license: str = ""
    compatibility: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    # Experimental Agent Skills field. Recorded and reported on activation; the permission system still decides every call.
    allowed_tools: tuple[str, ...] = ()
    # Extension of this harness (``disable-model-invocation``): hidden from the catalog and ActivateSkill, still a slash command.
    model_invocable: bool = True
    # A skill fetched from elsewhere (an MCP server) carries a catalog entry only; this returns the full skill when it
    # is activated, or raises SkillLoadError.
    remote: Callable[[Skill, Any], Awaitable[Skill]] | None = None

    @property
    def directory(self) -> Path | None:
        """The skill directory, or None for a single-file skill."""
        if self.source is not None and self.source.name == SKILL_DIR_FILENAME:
            return self.source.parent
        return None

    def bundled_files(self, limit: int = MAX_BUNDLED_FILES) -> tuple[list[str], bool]:
        """Relative POSIX paths of the files next to SKILL.md (sorted, at most *limit*) and whether the list was cut."""
        root = self.directory
        if root is None:
            return [], False
        found: list[str] = []
        for current, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIPPED_DIRS)
            for filename in sorted(filenames):
                relative = (Path(current) / filename).relative_to(root).as_posix()
                if relative == SKILL_DIR_FILENAME:
                    continue
                if len(found) >= limit:
                    return found, True
                found.append(relative)
        return found, False

    @classmethod
    def from_file(cls, path: Path, default_name: str | None = None) -> Skill:
        """Parse a skill file; ``default_name`` (the directory name) replaces the file stem when frontmatter has no name.

        Raises ValueError when the file has no usable frontmatter or no description.
        """
        text  = path.read_text(encoding="utf-8").lstrip("﻿")
        match = _FRONTMATTER.match(text)
        if match is None:
            raise ValueError(f"No frontmatter in {path}")
        meta        = _parse_frontmatter(match.group(1), path)
        description = _scalar_text(meta.get("description"))
        if not description:
            raise ValueError(f"No description in {path}")
        name = _scalar_text(meta.get("name"))
        if not name:
            name = default_name or path.stem
            logger.warning("Skill %s: no name in frontmatter; using %r", path, name)
        compatibility = _scalar_text(meta.get("compatibility"))
        _warn_nonconforming(path, name, default_name, description, compatibility)
        return cls(
            name             = name,
            description      = description,
            trigger          = _scalar_text(meta.get("trigger")) or f"/{name}",
            body             = text[match.end():].strip(),
            source           = path,
            license          = _scalar_text(meta.get("license")),
            compatibility    = compatibility,
            metadata         = _string_map(meta.get("metadata")),
            allowed_tools    = _tool_list(meta.get("allowed-tools")),
            model_invocable  = meta.get("disable-model-invocation") is not True,
        )


@dataclass(frozen=True)
class _Tier:
    """One skill directory: whether it sits inside the project and whether bare ``<name>.md`` files count."""

    path:    Path
    project: bool = False
    flat:    bool = False


def project_skill_trust(settings: Any) -> ProjectTrust:
    """The trust check for project skills: ``hooks.allow_project_hooks`` plus an approved digest of the file.

    Approvals are the ones ``nerdvana skill trust`` (or ``nerdvana hook trust``) records in the project hook
    trust file; editing a SKILL.md revokes its approval.
    """
    enabled = project_hooks_enabled(settings)

    def trusted(path: Path) -> bool:
        if not enabled:
            logger.warning("Project skill %s skipped: project-level skills are off (set hooks.allow_project_hooks)", path)
            return False
        try:
            digest = hook_digest(path)
        except OSError as exc:
            logger.warning("Project skill %s skipped: unreadable (%s)", path, exc)
            return False
        recorded = load_trust_record().get(str(path.resolve()))
        if recorded is None or not hmac.compare_digest(recorded, digest):
            logger.warning("Project skill %s skipped: not approved, or changed since approval (nerdvana skill trust)", path)
            return False
        return True

    return trusted


def _subdirectories(directory: Path) -> list[Path]:
    """Child directories worth scanning, sorted by name."""
    try:
        children = list(directory.iterdir())
    except OSError:
        return []
    return sorted(c for c in children if c.name not in SKIPPED_DIRS and c.is_dir())


def _skill_directories(top: Path, root: Path | None) -> list[Path]:
    """Directories below *top* that hold a SKILL.md, level by level, within the depth and directory bounds.

    A directory holding SKILL.md is a skill and is not searched further. With a *root*, a symlinked
    directory leading outside it is not searched either.
    """
    found: list[Path]              = []
    queue: deque[tuple[Path, int]] = deque([(top, 0)])
    seen                           = 0
    while queue:
        directory, depth = queue.popleft()
        for child in _subdirectories(directory):
            seen += 1
            if seen > MAX_SCAN_DIRS:
                logger.warning("Skill scan of %s stopped after %d directories", top, MAX_SCAN_DIRS)
                return found
            if (child / SKILL_DIR_FILENAME).is_file():
                found.append(child)
            elif depth + 1 < MAX_SCAN_DEPTH and (root is None or child.resolve().is_relative_to(root)):
                queue.append((child, depth + 1))
    return found


class SkillLoader:
    def __init__(
        self,
        project_dir: str = ".",
        global_dir: str | None = None,
        include_claude_skills: bool = False,
        claude_global_dir: str | None = None,
        agents_global_dir: str | None = None,
        project_trust: ProjectTrust | None = None,
    ):
        self._project_dir           = Path(project_dir)
        self._global_dir            = Path(global_dir) if global_dir else paths.user_skills_dir()
        self._include_claude_skills = include_claude_skills
        self._claude_global_dir     = (
            Path(claude_global_dir) if claude_global_dir else Path.home() / ".claude" / "skills"
        )
        self._agents_global_dir     = (
            Path(agents_global_dir) if agents_global_dir else Path.home() / ".agents" / "skills"
        )
        # None loads project skills unconditionally; from_settings supplies the trust check.
        self._project_trust         = project_trust
        self._skills: list[Skill]   = []
        self._activated: set[str]   = set()

    @classmethod
    def from_settings(cls, settings: Any) -> SkillLoader:
        """A loader for the session's project, with project skills gated on trust."""
        return cls(
            project_dir           = getattr(settings, "cwd", "") or ".",
            include_claude_skills = bool(getattr(getattr(settings, "skills", None), "include_claude_skills", False)),
            project_trust         = project_skill_trust(settings),
        )

    def _tiers(self) -> list[_Tier]:
        """Skill directories in ascending precedence (later tiers override earlier ones)."""
        claude  = self._include_claude_skills
        project = self._project_dir
        tiers   = [_Tier(Path(__file__).parent.parent / "skills", flat=True)]
        if claude:
            tiers.append(_Tier(self._claude_global_dir, flat=True))
        tiers.append(_Tier(self._agents_global_dir))
        tiers.append(_Tier(self._global_dir, flat=True))
        if claude:
            tiers.append(_Tier(project / ".claude" / "skills", project=True, flat=True))
        tiers.append(_Tier(project / ".agents" / "skills", project=True))
        tiers.append(_Tier(project / ".nerdvana" / "skills", project=True, flat=True))
        return tiers

    def load_all(self) -> list[Skill]:
        skills_by_name: dict[str, Skill] = {}
        for tier in self._tiers():
            for skill in self._load_dir(tier):
                shadowed = skills_by_name.get(skill.name)
                if shadowed is not None:
                    logger.warning("Skill %r from %s shadows %s", skill.name, skill.source, shadowed.source)
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

    def model_skills(self) -> list[Skill]:
        """The skills the model may activate, ordered by name so the catalog is stable between runs."""
        return sorted((s for s in self._skills if s.model_invocable), key=lambda s: s.name)

    def add_skills(self, skills: Iterable[Skill]) -> None:
        """Add skills that do not come from a skill directory; a name that is taken keeps its first skill."""
        for skill in skills:
            if self.get_by_name(skill.name) is None:
                self._skills.append(skill)
            else:
                logger.warning("Skill %r from %s is not added: the name is taken", skill.name, skill.trigger)

    def is_activated(self, name: str) -> bool:
        """Whether *name* is in the conversation."""
        return name in self._activated

    def mark_activated(self, name: str) -> bool:
        """Record that *name* is in the conversation; False when it already was."""
        if name in self._activated:
            return False
        self._activated.add(name)
        return True

    def reset_activations(self) -> None:
        """Forget what was activated, for a conversation that starts over."""
        self._activated.clear()

    def _load_dir(self, tier: _Tier) -> list[Skill]:
        """Load one tier: single-file skills first, then directory skills (which win on a name clash)."""
        if not tier.path.is_dir():
            return []
        # A project tier is code from the repository: its symlinks must stay inside it. User tiers may link out,
        # as skill installers do (~/.agents/skills/<name> -> a central store).
        root = tier.path.resolve() if tier.project else None
        candidates: list[tuple[Path, str | None]] = []
        if tier.flat:
            candidates.extend((path, None) for path in sorted(tier.path.glob("*.md")))
        candidates.extend((d / SKILL_DIR_FILENAME, d.name) for d in _skill_directories(tier.path, root))
        by_name: dict[str, Skill] = {}
        for path, default_name in candidates:
            skill = self._load_skill(path, root, default_name, tier)
            if skill is None:
                continue
            clash = by_name.get(skill.name)
            if clash is not None:
                logger.warning(
                    "Skill %r defined as both %s and %s; using the later one",
                    skill.name, clash.source, path,
                )
            by_name[skill.name] = skill
        return list(by_name.values())

    def _load_skill(self, path: Path, root: Path | None, default_name: str | None, tier: _Tier) -> Skill | None:
        """Parse one skill file, or return None when it escapes the tier, is oversize, untrusted, or invalid."""
        try:
            resolved = path.resolve()
            if root is not None and not resolved.is_relative_to(root):
                logger.warning("Skipping skill %s: resolves outside %s", path, root)
                return None
            size = resolved.stat().st_size
            if size > MAX_SKILL_BYTES:
                logger.warning("Skipping skill %s: %d bytes exceeds %d", path, size, MAX_SKILL_BYTES)
                return None
            if tier.project and self._project_trust is not None and not self._project_trust(path):
                return None
            return Skill.from_file(path, default_name=default_name)
        except Exception as e:
            logger.warning("Failed to load skill %s: %s", path, e)
            return None
