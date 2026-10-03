"""Checks and records around an edit tool call: the edit scope, the goal scope and the checkpoint before it.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Any

from nerdvana_cli.core.safety.edit_tools import EDIT_PATH_ATTRS, EDIT_TOOL_NAMES
from nerdvana_cli.core.safety.tool_permission import ask_user_permission, refusal
from nerdvana_cli.core.signals import OUT_OF_GOAL_SCOPE
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.types import ToolResult

logger = logging.getLogger(__name__)


def edit_targets(parsed_args: Any) -> list[str]:
    """Return the file paths carried by a parsed edit-tool argument object."""
    for attr in EDIT_PATH_ATTRS:
        value = getattr(parsed_args, attr, None)
        if isinstance(value, str) and value.strip():
            return [value]
    return []


def applies_edit(tool_name: str, parsed_args: Any) -> bool:
    """True for an edit tool call that changes files (a symbol edit that is only a preview does not)."""
    return tool_name in EDIT_TOOL_NAMES and bool(getattr(parsed_args, "apply", True))


def check_edit_scope(tool_use: dict[str, Any], parsed_args: Any, context: ToolContext) -> ToolResult | None:
    """Refuse an edit outside ``sandbox.edit_scope``; None means allowed or no scope is set."""
    scope = context.state.get("edit_scope")
    if scope is None or not applies_edit(tool_use["name"], parsed_args):
        return None
    root    = Path(context.cwd).resolve()
    allowed = [(root / entry).resolve() for entry in scope]
    for target in edit_targets(parsed_args):
        resolved = (root / target).resolve()
        if not any(resolved == base or base in resolved.parents for base in allowed):
            where = ", ".join(scope) if scope else "nowhere"
            return refusal(tool_use["id"], f"Outside this agent's edit scope ({where}): {target}")
    return None


async def check_goal_scope(
    tool_use:    dict[str, Any],
    parsed_args: Any,
    context:     ToolContext,
    counts:      Counter[str],
) -> ToolResult | None:
    """Ask before an edit outside the goal's scope; refuse it when nobody can be asked. None means go ahead.

    A goal's scope says what the work is about. Leaving it is allowed, but a person decides. Each edit
    that leaves it is counted in *counts*.
    """
    scope = context.state.get("goal_scope")
    if not scope or not applies_edit(tool_use["name"], parsed_args):
        return None
    root    = Path(context.cwd).resolve()
    allowed = [(root / entry).resolve() for entry in scope]
    outside = [t for t in edit_targets(parsed_args) if not any((root / t).resolve() == b or b in (root / t).resolve().parents for b in allowed)]
    if not outside:
        return None
    counts[OUT_OF_GOAL_SCOPE] += 1
    message = f"This edit is outside the goal's scope ({', '.join(scope)}): {', '.join(outside)}"
    if await ask_user_permission(context=context, tool_name=tool_use["name"], message=message):
        return None
    return refusal(tool_use["id"], f"Permission denied by user: {message}")


def capture_checkpoint(checkpoint_manager: Any, tool_name: str, parsed_args: Any) -> None:
    """Snapshot the files *tool_name* is about to change.

    Passing the edit targets is what arms undo: ``before_edit`` copies
    nothing when it receives no path. Every failure below is logged instead
    of suppressed, because an undo facility that captures nothing while
    appearing armed is the data-loss risk it exists to remove.
    """
    try:
        if not getattr(parsed_args, "apply", True):
            # Preview-only symbol edit: nothing on disk changes.
            return
        targets = edit_targets(parsed_args)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "checkpoint: could not read the edit targets of %s (%s); "
            "undo will not cover this edit",
            tool_name,
            exc,
        )
        return

    if not targets:
        logger.warning(
            "checkpoint: %s exposed no edit target; undo will not cover this edit",
            tool_name,
        )
        return

    try:
        checkpoint_manager.before_edit(tool_name, targets)
    except Exception as exc:  # noqa: BLE001
        logger.warning("checkpoint: capture failed before %s: %s", tool_name, exc)
