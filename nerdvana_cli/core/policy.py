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
import re
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


# Rules are a tool name pattern, or a tool name pattern with a pattern for the call's main argument:
# ``FileRead``, ``lsp_*``, ``Bash(git status)``, ``Bash(git diff *)``, ``FileEdit(docs/*)``.
_RULE = re.compile(r"^(?P<name>[^()]+)\((?P<argument>.*)\)$")
_SHELL_TOOLS = frozenset({"Bash", "Parism"})
# With any of these a command does more than its first words say, so an allow rule cannot vouch for it.
_SHELL_META = re.compile(r"[;&|<>$`()\n\\]")
_ARGUMENT_KEYS = ("command", "path", "relative_path", "file_path", "url")


def primary_argument(tool_name: str, tool_input: dict[str, Any] | None) -> str | None:
    """The argument a rule's pattern is matched against: the command of a shell tool, the path of a file tool, a URL."""
    if not isinstance(tool_input, dict):
        return None
    for key in _ARGUMENT_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _matches(name: str, patterns: list[str], argument: str | None = None, allow: bool = False) -> bool:
    """True when a rule matches the call.

    A rule with an argument pattern needs an argument. For an *allow* rule on a shell tool the command must
    be simple: a glob like ``git status*`` also matches ``git status; rm -rf x``, so commands with shell
    operators never match an allow rule that names arguments. A deny rule has no such restriction.
    """
    for pattern in patterns:
        rule = _RULE.match(pattern)
        if rule is None:
            if fnmatch.fnmatchcase(name, pattern):
                return True
            continue
        if argument is None or not fnmatch.fnmatchcase(name, rule["name"].strip()):
            continue
        if allow and name in _SHELL_TOOLS and _SHELL_META.search(argument):
            continue
        if fnmatch.fnmatchcase(argument, rule["argument"]):
            return True
    return False


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

    def decide(self, tool: BaseTool[Any], verdict: PermissionResult, tool_input: dict[str, Any] | None = None) -> PermissionResult:
        """Combine the tool's own *verdict* with the user's rules; *tool_input* lets a rule name arguments."""
        name     = tool.name
        argument = primary_argument(name, tool_input)

        if _matches(name, self.always_deny, argument):
            return PermissionResult(PermissionBehavior.DENY, f"{name} is listed in permissions.always_deny")
        if not self.is_visible(name):
            return PermissionResult(PermissionBehavior.DENY, f"{name} is not available in mode '{self.mode_name}'")
        if verdict.behavior == PermissionBehavior.DENY:
            return verdict
        if _matches(name, self.always_allow, argument, allow=True):
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
