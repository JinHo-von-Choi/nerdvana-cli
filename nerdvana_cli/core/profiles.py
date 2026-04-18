"""Runtime profiles — context × mode 2-axis YAML profile system.

작성자: 최진호
작성일: 2026-04-18

Two orthogonal axes:
  - ContextProfile: describes the harness environment (Claude Code, VS Code, standalone…).
  - ModeProfile:    describes the current task type (planning, editing, one-shot…).

ProfileManager combines both to produce a unified tool-visibility filter and
merged prompt fragments that are injected into every agent turn.

Storage resolution (highest priority first):
  1. <cwd>/.nerdvana/contexts/*.yml  /  <cwd>/.nerdvana/modes/*.yml
  2. ~/.nerdvana/contexts/*.yml       /  ~/.nerdvana/modes/*.yml
  3. Built-in defaults shipped with the package under
     nerdvana_cli/resources/profiles/{contexts,modes}/*.yml
"""

from __future__ import annotations

import importlib.resources
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from nerdvana_cli.core.tool import BaseTool, ToolRegistry

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

TrustLevel  = Literal["strict", "balanced", "yolo"]
_TRUST_VALS = {"strict", "balanced", "yolo"}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ContextProfile:
    """Describes the harness environment (e.g. standalone, claude-code, vscode)."""

    name:                       str
    description:                str               = ""
    prompt_override:            str | None        = None
    prompt_append:              str | None        = None
    excluded_tools:             list[str]         = field(default_factory=list)
    included_tools:             list[str]         = field(default_factory=list)
    tool_description_overrides: dict[str, str]    = field(default_factory=dict)
    single_project:             bool              = False

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> ContextProfile:
        return cls(
            name                       = name,
            description                = data.get("description", ""),
            prompt_override            = data.get("prompt_override"),
            prompt_append              = data.get("prompt_append"),
            excluded_tools             = list(data.get("excluded_tools") or []),
            included_tools             = list(data.get("included_tools") or []),
            tool_description_overrides = dict(data.get("tool_description_overrides") or {}),
            single_project             = bool(data.get("single_project", False)),
        )


@dataclass
class ModeProfile:
    """Describes the current task mode (e.g. planning, editing, one-shot)."""

    name:                       str
    description:                str               = ""
    prompt_override:            str | None        = None
    prompt_append:              str | None        = None
    excluded_tools:             list[str]         = field(default_factory=list)
    included_tools:             list[str]         = field(default_factory=list)
    tool_description_overrides: dict[str, str]    = field(default_factory=dict)
    model_override:             str | None        = None
    trust_level:                TrustLevel        = "balanced"

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> ModeProfile:
        raw_trust = data.get("trust_level", "balanced")
        trust: TrustLevel = raw_trust if raw_trust in _TRUST_VALS else "balanced"
        return cls(
            name                       = name,
            description                = data.get("description", ""),
            prompt_override            = data.get("prompt_override"),
            prompt_append              = data.get("prompt_append"),
            excluded_tools             = list(data.get("excluded_tools") or []),
            included_tools             = list(data.get("included_tools") or []),
            tool_description_overrides = dict(data.get("tool_description_overrides") or {}),
            model_override             = data.get("model_override"),
            trust_level                = trust,
        )


# ---------------------------------------------------------------------------
# Merged (synthesized) profile
# ---------------------------------------------------------------------------

@dataclass
class MergedProfile:
    """Result of combining a ContextProfile and a ModeProfile."""

    context_name:               str
    mode_name:                  str
    prompt_override:            str | None
    prompt_append:              str | None        # context append + mode append (newline-joined)
    excluded_tools:             frozenset[str]
    included_tools:             frozenset[str]    # empty means "all minus excluded"
    tool_description_overrides: dict[str, str]
    model_override:             str | None
    trust_level:                TrustLevel
    single_project:             bool


# ---------------------------------------------------------------------------
# ProfileManager
# ---------------------------------------------------------------------------

class ProfileManager:
    """Loads, stores, and synthesises context + mode profiles.

    The manager holds a *stack* of active modes so callers can push/pop
    temporary mode overrides (e.g. /mode planning → /mode editing).
    The context is a single active profile (no stacking needed).
    """

    DEFAULT_CONTEXT = "standalone"
    DEFAULT_MODE    = "interactive"

    def __init__(self, cwd: str = ".") -> None:
        self._cwd:            str                      = cwd
        self._context_cache:  dict[str, ContextProfile] = {}
        self._mode_cache:     dict[str, ModeProfile]    = {}
        self._active_context: str                      = self.DEFAULT_CONTEXT
        self._mode_stack:     list[str]                = [self.DEFAULT_MODE]

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    @property
    def active_context_name(self) -> str:
        return self._active_context

    @property
    def active_mode_name(self) -> str:
        return self._mode_stack[-1] if self._mode_stack else self.DEFAULT_MODE

    @property
    def mode_stack(self) -> list[str]:
        return list(self._mode_stack)

    # ------------------------------------------------------------------
    # Context operations
    # ------------------------------------------------------------------

    def set_context(self, name: str) -> ContextProfile:
        """Activate a context profile by name. Raises ValueError if unknown."""
        profile = self.load_context(name)
        self._active_context = name
        return profile

    def load_context(self, name: str) -> ContextProfile:
        """Load a ContextProfile by name (with caching)."""
        if name not in self._context_cache:
            self._context_cache[name] = self._load_profile(name, "contexts", ContextProfile)
        return self._context_cache[name]

    # ------------------------------------------------------------------
    # Mode operations
    # ------------------------------------------------------------------

    def push_mode(self, name: str) -> ModeProfile:
        """Push a mode onto the stack and return it."""
        profile = self.load_mode(name)
        self._mode_stack.append(name)
        return profile

    def pop_mode(self) -> str | None:
        """Pop the top mode. Returns the removed mode name, or None if stack had only the default."""
        if len(self._mode_stack) <= 1:
            return None
        return self._mode_stack.pop()

    def set_mode(self, name: str) -> ModeProfile:
        """Replace the entire mode stack with a single mode."""
        profile = self.load_mode(name)
        self._mode_stack = [name]
        return profile

    def load_mode(self, name: str) -> ModeProfile:
        """Load a ModeProfile by name (with caching)."""
        if name not in self._mode_cache:
            self._mode_cache[name] = self._load_profile(name, "modes", ModeProfile)
        return self._mode_cache[name]

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    def merged(self) -> MergedProfile:
        """Synthesise the active context + mode into a MergedProfile."""
        ctx  = self.load_context(self._active_context)
        mode = self.load_mode(self.active_mode_name)

        # prompt_override: mode wins, then context, then None
        prompt_override = mode.prompt_override or ctx.prompt_override

        # prompt_append: concat both (non-None parts)
        parts = [p for p in (ctx.prompt_append, mode.prompt_append) if p]
        prompt_append = "\n".join(parts) if parts else None

        # excluded: union
        excluded = frozenset(ctx.excluded_tools) | frozenset(mode.excluded_tools)

        # included: if mode specifies, mode wins; else use context
        included = frozenset(mode.included_tools) if mode.included_tools else frozenset(ctx.included_tools)

        # tool description overrides: context base, mode overrides
        desc_overrides = {**ctx.tool_description_overrides, **mode.tool_description_overrides}

        return MergedProfile(
            context_name               = self._active_context,
            mode_name                  = self.active_mode_name,
            prompt_override            = prompt_override,
            prompt_append              = prompt_append,
            excluded_tools             = excluded,
            included_tools             = included,
            tool_description_overrides = desc_overrides,
            model_override             = mode.model_override,
            trust_level                = mode.trust_level,
            single_project             = ctx.single_project,
        )

    def visible_tools(self, registry: ToolRegistry) -> list[BaseTool[Any]]:
        """Return the subset of registered tools visible under the current profiles.

        Visibility logic (O(n)):
          1. If included_tools is non-empty, only those tool names survive.
          2. Then remove excluded_tools.
        """
        profile   = self.merged()
        all_tools = registry.all_tools()

        if profile.included_tools:
            tools = [t for t in all_tools if t.name in profile.included_tools]
        else:
            tools = list(all_tools)

        return [t for t in tools if t.name not in profile.excluded_tools]

    def current_config_summary(self) -> dict[str, Any]:
        """Return a dict summarising the current active profile (for GetCurrentConfig tool)."""
        m = self.merged()
        return {
            "context":       m.context_name,
            "mode":          m.mode_name,
            "mode_stack":    list(self._mode_stack),
            "trust_level":   m.trust_level,
            "model_override": m.model_override,
            "excluded_tools": sorted(m.excluded_tools),
            "included_tools": sorted(m.included_tools),
            "single_project": m.single_project,
            "prompt_override": m.prompt_override,
            "prompt_append":   m.prompt_append,
        }

    # ------------------------------------------------------------------
    # Available profile names
    # ------------------------------------------------------------------

    def available_contexts(self) -> list[str]:
        """Return sorted list of all discoverable context profile names."""
        return sorted(self._discover_names("contexts"))

    def available_modes(self) -> list[str]:
        """Return sorted list of all discoverable mode profile names."""
        return sorted(self._discover_names("modes"))

    # ------------------------------------------------------------------
    # Internal loading machinery
    # ------------------------------------------------------------------

    def _load_profile(
        self,
        name: str,
        kind: str,              # "contexts" or "modes"
        cls:  Any,              # ContextProfile or ModeProfile — both have from_dict()
    ) -> Any:
        """Load a profile YAML from the first matching location."""
        data = self._read_yaml(name, kind)
        return cls.from_dict(name, data)

    def _read_yaml(self, name: str, kind: str) -> dict[str, Any]:
        """Return the raw YAML dict for *name* from the highest-priority source.

        Resolution order (first found wins):
          1. <cwd>/.nerdvana/<kind>/<name>.yml
          2. ~/.nerdvana/<kind>/<name>.yml
          3. Built-in package resources
        """
        filename = f"{name}.yml"
        candidates: list[Path | None] = [
            Path(self._cwd) / ".nerdvana" / kind / filename,
            Path.home() / ".nerdvana" / kind / filename,
        ]
        for candidate in candidates:
            if candidate and candidate.exists():
                with candidate.open() as fh:
                    return yaml.safe_load(fh) or {}

        # Fall back to built-in package resources
        return self._load_builtin(kind, filename, name)

    def _load_builtin(self, kind: str, filename: str, name: str) -> dict[str, Any]:
        """Load a built-in profile YAML from the package resources directory."""
        try:
            pkg  = f"nerdvana_cli.resources.profiles.{kind}"
            ref  = importlib.resources.files(pkg).joinpath(filename)
            text = ref.read_text(encoding="utf-8")
            return yaml.safe_load(text) or {}
        except (FileNotFoundError, ModuleNotFoundError, AttributeError):
            # importlib.resources.files requires Python 3.9+; fallback to __file__
            here = Path(__file__).parent.parent / "resources" / "profiles" / kind / filename
            if here.exists():
                with here.open() as fh:
                    return yaml.safe_load(fh) or {}
            raise ValueError(f"Profile '{name}' not found in {kind}") from None

    def _discover_names(self, kind: str) -> set[str]:
        """Collect all profile names visible from any source tier."""
        names: set[str] = set()

        # Project-local
        proj_dir = Path(self._cwd) / ".nerdvana" / kind
        if proj_dir.is_dir():
            names.update(p.stem for p in proj_dir.glob("*.yml"))

        # User-global
        user_dir = Path.home() / ".nerdvana" / kind
        if user_dir.is_dir():
            names.update(p.stem for p in user_dir.glob("*.yml"))

        # Built-in
        builtin_dir = Path(__file__).parent.parent / "resources" / "profiles" / kind
        if builtin_dir.is_dir():
            names.update(p.stem for p in builtin_dir.glob("*.yml"))

        return names
