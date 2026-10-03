"""Whether a tool call may run: the permission policy, and the user when the policy asks them.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from typing import TYPE_CHECKING, Any

from nerdvana_cli.core.approvals import normalise
from nerdvana_cli.core.policy import PermissionPolicy, primary_argument
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.core.untrusted import UntrustedTracker
from nerdvana_cli.types import PermissionBehavior, ToolResult

if TYPE_CHECKING:
    from nerdvana_cli.core.analytics import AnalyticsWriter

logger = logging.getLogger(__name__)


def refusal(tool_id: str, content: str) -> ToolResult:
    """An error result that tells the model a call was not run, and why."""
    return ToolResult(tool_use_id=tool_id, content=content, is_error=True)


class PermissionGate:
    """Applies the permission policy to one call and asks the user when it says so."""

    def __init__(self, policy: PermissionPolicy, untrusted: UntrustedTracker, analytics_writer: AnalyticsWriter | None) -> None:
        self._policy           = policy
        self._untrusted        = untrusted
        self._analytics_writer = analytics_writer

    async def check(
        self,
        tool_use:    dict[str, Any],
        tool:        Any,
        parsed_args: Any,
        context:     ToolContext,
    ) -> ToolResult | None:
        """Apply the permission policy; ask the user when it says so. None means allowed."""
        tool_id     = tool_use["id"]
        perm_result = self._policy.decide(tool, tool.check_permissions(parsed_args, context), tool_use["input"])
        perm_result = self._untrusted.gate(tool, tool_use["input"], perm_result, self._policy.trust_level)
        if perm_result.behavior == PermissionBehavior.DENY:
            return refusal(tool_id, f"Permission denied: {perm_result.message}")
        if perm_result.behavior == PermissionBehavior.ASK:
            granted = await ask_user_permission(
                context   = context,
                tool_name = tool_use["name"],
                message   = self._with_preview(tool, parsed_args, context, perm_result.message),
            )
            self._record_answer(tool_use, granted)
            if not granted:
                return refusal(tool_id, f"Permission denied by user: {perm_result.message}")
        return None

    def _record_answer(self, tool_use: dict[str, Any], granted: bool) -> None:
        """Keep the user's answer so repeated approvals can be offered as rules (``nerdvana approvals``)."""
        if self._analytics_writer is None:
            return
        with contextlib.suppress(Exception):
            self._analytics_writer.record_approval(tool_use["name"], normalise(primary_argument(tool_use["name"], tool_use["input"])), granted)

    @staticmethod
    def _with_preview(tool: Any, parsed_args: Any, context: ToolContext, message: str) -> str:
        """Append the change a tool would make to *message*, when the tool can compute it.

        Any failure just means no preview: the question is still asked.
        """
        preview = getattr(tool, "preview_change", None)
        if preview is None:
            return message
        try:
            detail = preview(parsed_args, context)
        except Exception:  # noqa: BLE001
            logger.debug("no preview for %s", getattr(tool, "name", "?"), exc_info=True)
            return message
        return f"{message}\n\n{detail}" if detail else message


async def ask_user_permission(
    tool_name: str,
    message:   str,
    context:   ToolContext | None = None,
) -> bool:
    """Prompt the user for explicit confirmation when a tool returns ASK.

    Behaviour:
    - A front end that supplied ``context.confirm`` (the TUI) decides; the
      terminal is never touched, because a full-screen app owns it.
    - Interactive TTY: prints a y/N prompt and reads a single line.
      Accepts "y" or "yes" (case-insensitive); everything else is DENY.
      An empty reply defaults to N (fail-safe).
    - Non-interactive (piped stdin / CI / batch): immediately returns False
      (DENY) without blocking: safe default prevents unattended approval.

    Returns True only when the user explicitly confirms with y/yes.
    """
    confirm = getattr(context, "confirm", None)
    if confirm is not None:
        try:
            granted = bool(await confirm(tool_name, message))
        except Exception:  # noqa: BLE001
            logger.exception("confirmation front end failed for %s; denying", tool_name)
            granted = False
        logger.info("ASK permission for %s via front end: %s", tool_name, "ALLOW" if granted else "DENY")
        return granted

    prompt_text = (
        f"\n[permission] {tool_name}: {message}\n"
        "Allow this action? [y/N] "
    )

    if not sys.stdin.isatty():
        # Non-interactive session: fail-safe DENY, never block.
        logger.info(
            "ASK permission for %s auto-denied: non-interactive session (no TTY)",
            tool_name,
        )
        return False

    try:
        # Run blocking input() in a thread so we don't stall the event loop.
        loop    = asyncio.get_running_loop()
        reply   = await loop.run_in_executor(None, lambda: input(prompt_text))
        granted = reply.strip().lower() in {"y", "yes"}
    except (EOFError, OSError):
        # stdin closed unexpectedly: treat as DENY.
        granted = False

    logger.info(
        "ASK permission for %s: user replied %r → %s",
        tool_name,
        reply if "reply" in dir() else "<eof>",
        "ALLOW" if granted else "DENY",
    )
    return granted
