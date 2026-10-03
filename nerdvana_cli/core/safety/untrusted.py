"""Holding a command or a state-changing call for approval when it repeats text from untrusted output.

Author: 최진호
Date:   2026-10-03

Text that came back from the web or from an external MCP server is not written by the user, and it can
carry instructions. The usual way such an instruction does harm is that the model copies a command or an
argument out of it into a tool that runs or changes something. ``UntrustedTracker`` remembers what those
sources returned and, when a ``Bash`` command or a state-changing MCP call repeats a stretch of it
verbatim, turns an allowed call into a question for the user.

It is a deterministic check on the arguments, not a judgement of the content: it does not stop a model
that rewrites the instruction in its own words, so it comes on top of the sandbox and the permission
rules, not instead of them. ``yolo`` trust runs without it by design.
"""

from __future__ import annotations

from collections import Counter, deque
from typing import Any

from nerdvana_cli.core.state.signals import UNTRUSTED_SOURCE
from nerdvana_cli.types import PermissionBehavior, PermissionResult

UNTRUSTED_TOOLS = frozenset({"WebFetch", "WebSearch"})
MCP_PREFIX      = "mcp__"

# A shared run of at least this many characters counts as a copy; windows of that size are tried at a
# stride, so a run of WINDOW + STRIDE - 1 characters is always caught.
WINDOW      = 24
STRIDE      = 8
KEPT_OUTPUTS = 20
MAX_CHARS    = 200_000


def is_untrusted_source(tool: Any) -> bool:
    """Output of this tool is text written by someone other than the user."""
    name = str(getattr(tool, "name", ""))
    return name in UNTRUSTED_TOOLS or name.startswith(MCP_PREFIX)


def is_sink(tool: Any) -> bool:
    """A call that runs commands or changes state somewhere: ``Bash`` and MCP tools not marked read-only."""
    name = str(getattr(tool, "name", ""))
    return name == "Bash" or (name.startswith(MCP_PREFIX) and not getattr(tool, "is_concurrency_safe", False))


def _squash(text: str) -> str:
    return " ".join(text.split())


def _flatten(value: Any) -> str:
    """Every string inside a tool input, joined, so nested arguments are checked too."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten(item) for item in value)
    return ""


class UntrustedTracker:
    """Remembers recent output of untrusted tools and checks sink calls against it."""

    def __init__(self, enabled: bool = True, signals: Counter[str] | None = None) -> None:
        self._enabled = enabled
        self._signals = signals if signals is not None else Counter()
        self._outputs: deque[tuple[str, str]] = deque(maxlen=KEPT_OUTPUTS)

    @classmethod
    def from_settings(cls, settings: Any, signals: Counter[str] | None = None) -> UntrustedTracker:
        permissions = getattr(settings, "permissions", None)
        return cls(bool(getattr(permissions, "gate_untrusted_sources", True)), signals)

    def record(self, tool: Any, content: str) -> None:
        """Keep what an untrusted tool returned."""
        if self._enabled and content and is_untrusted_source(tool):
            self._outputs.append((str(tool.name), _squash(content)[:MAX_CHARS]))

    def source_of(self, tool_input: dict[str, Any]) -> str:
        """The tool whose remembered output shares a verbatim run with *tool_input*, or an empty string."""
        text = _squash(_flatten(tool_input))
        if len(text) < WINDOW:
            return ""
        for start in range(0, len(text) - WINDOW + 1, STRIDE):
            window = text[start:start + WINDOW]
            for source, output in self._outputs:
                if window in output:
                    return source
        return ""

    def gate(self, tool: Any, tool_input: dict[str, Any], verdict: PermissionResult, trust_level: str) -> PermissionResult:
        """*verdict*, or a question when an allowed sink call repeats untrusted text (never relaxes a verdict)."""
        if not self._enabled or trust_level == "yolo" or verdict.behavior != PermissionBehavior.ALLOW or not is_sink(tool):
            return verdict
        source = self.source_of(tool_input)
        if not source:
            return verdict
        self._signals[UNTRUSTED_SOURCE] += 1
        message = f"{tool.name} repeats text from {source} output, which was not written by you. Run it only if you meant it."
        return PermissionResult(PermissionBehavior.ASK, message, verdict.updated_input)
