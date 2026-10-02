"""Central permission policy applied to every tool call.

Author: 최진호
Date:   2026-10-03

A tool's own ``check_permissions`` verdict is one input. The policy combines
it with the user's configuration in a fixed order, so the main loop,
sub-agents and background tasks all reach the same decision for the same call:

  1. ``permissions.always_deny`` pattern match          -> DENY
  2. tool excluded by the active mode profile            -> DENY
  3. tool's own verdict is DENY                          -> DENY
  4. ``permissions.always_allow`` pattern match          -> ALLOW
  5. trust level of the active mode profile:
       strict   : WRITE / DESTRUCTIVE tools, and the above -> ASK
       balanced : DESTRUCTIVE tools and tools declaring
                  ``requires_confirmation``              -> at least ASK
       yolo     : ASK                                    -> ALLOW
  6. otherwise the tool's own verdict
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nerdvana_cli.core.tool import ToolCategory
from nerdvana_cli.types import PermissionBehavior, PermissionResult

if TYPE_CHECKING:
    from nerdvana_cli.core.tool import BaseTool

logger = logging.getLogger(__name__)

# ``permissions.mode`` values from the YAML config, mapped onto mode profiles.
_PERMISSION_MODE_TO_PROFILE: dict[str, str] = {
    "default":      "interactive",
    "accept-edits": "editing",
    "bypass":       "one-shot",
    "plan":         "planning",
}

_DEFAULT_MODE = "interactive"

_MUTATING_CATEGORIES: frozenset[ToolCategory] = frozenset({ToolCategory.WRITE, ToolCategory.DESTRUCTIVE})


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


@dataclass
class PermissionPolicy:
    """User-level permission rules resolved once per agent loop."""

    mode_name:      str       = _DEFAULT_MODE
    trust_level:    str       = "balanced"
    always_allow:   list[str] = field(default_factory=list)
    always_deny:    list[str] = field(default_factory=list)
    excluded_tools: list[str] = field(default_factory=list)
    included_tools: list[str] = field(default_factory=list)

    @classmethod
    def from_settings(cls, settings: Any) -> PermissionPolicy:
        """Build the policy from ``NerdvanaSettings``.

        ``session.default_mode`` (set by ``--approval-mode`` or the YAML) wins
        over ``permissions.mode``; an unknown mode name falls back to the
        default mode with a warning instead of failing the session.
        """
        from nerdvana_cli.core.profiles import ProfileManager

        permissions = getattr(settings, "permissions", None)
        session     = getattr(settings, "session", None)
        mode_name   = getattr(session, "default_mode", "") or _DEFAULT_MODE
        if mode_name == _DEFAULT_MODE and permissions is not None:
            mode_name = _PERMISSION_MODE_TO_PROFILE.get(permissions.mode, _DEFAULT_MODE)

        manager = ProfileManager(cwd=getattr(settings, "cwd", "") or ".")
        try:
            profile = manager.load_mode(mode_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unknown mode %r (%s); using %r", mode_name, exc, _DEFAULT_MODE)
            mode_name = _DEFAULT_MODE
            profile   = manager.load_mode(mode_name)

        return cls(
            mode_name      = mode_name,
            trust_level    = profile.trust_level,
            always_allow   = list(getattr(permissions, "always_allow", []) or []),
            always_deny    = list(getattr(permissions, "always_deny", []) or []),
            excluded_tools = list(profile.excluded_tools),
            included_tools = list(profile.included_tools),
        )

    def is_visible(self, tool_name: str) -> bool:
        """True when the active mode exposes *tool_name* to the model."""
        if self.included_tools and tool_name not in self.included_tools:
            return False
        return tool_name not in self.excluded_tools

    def decide(self, tool: BaseTool[Any], verdict: PermissionResult) -> PermissionResult:
        """Combine the tool's own *verdict* with the user's rules."""
        name = tool.name

        if _matches(name, self.always_deny):
            return PermissionResult(PermissionBehavior.DENY, f"{name} is listed in permissions.always_deny")
        if not self.is_visible(name):
            return PermissionResult(PermissionBehavior.DENY, f"{name} is not available in mode '{self.mode_name}'")
        if verdict.behavior == PermissionBehavior.DENY:
            return verdict
        if _matches(name, self.always_allow):
            return PermissionResult(PermissionBehavior.ALLOW, verdict.message, verdict.updated_input)

        category = getattr(tool, "category", ToolCategory.READ)
        if self.trust_level == "yolo":
            return PermissionResult(PermissionBehavior.ALLOW, verdict.message, verdict.updated_input)
        if verdict.behavior == PermissionBehavior.ALLOW:
            asks = (
                category == ToolCategory.DESTRUCTIVE
                or bool(getattr(tool, "requires_confirmation", False))
                or (self.trust_level == "strict" and category in _MUTATING_CATEGORIES)
            )
            if asks:
                return PermissionResult(
                    PermissionBehavior.ASK,
                    verdict.message or f"{name} changes state (mode '{self.mode_name}', trust {self.trust_level})",
                    verdict.updated_input,
                )
        return verdict
