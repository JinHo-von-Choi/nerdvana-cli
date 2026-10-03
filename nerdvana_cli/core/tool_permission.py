"""Whether a tool call may run: the permission policy, and the user when the policy asks them.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from collections import Counter
from typing import TYPE_CHECKING, Any

from nerdvana_cli.core.approvals import normalise
from nerdvana_cli.core.classifier import ALLOW, ASK, DENY, ENFORCE, SHADOW, ActionClassifier, Classification
from nerdvana_cli.core.hooks import HookEvent
from nerdvana_cli.core.policy import PermissionPolicy, primary_argument
from nerdvana_cli.core.signals import CLASSIFIER_ASK, CLASSIFIER_DENY, CLASSIFIER_ERROR
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.core.untrusted import UntrustedTracker
from nerdvana_cli.types import PermissionBehavior, ToolResult

if TYPE_CHECKING:
    from nerdvana_cli.core.hooks import HookEngine
    from nerdvana_cli.core.telemetry.analytics import AnalyticsWriter

logger = logging.getLogger(__name__)


def refusal(tool_id: str, content: str) -> ToolResult:
    """An error result that tells the model a call was not run, and why."""
    return ToolResult(tool_use_id=tool_id, content=content, is_error=True)


class PermissionGate:
    """Applies the permission policy to one call and asks the user when it says so.

    With an action classifier (``permissions.classifier``) it also has each call that would run unasked judged
    by a second model: in ``shadow`` mode the verdicts are only recorded, in ``enforce`` mode an allow becomes
    an ask or a refusal. A refusal, whoever made it, fires the PERMISSION_DENIED hook.
    """

    def __init__(
        self,
        policy:           PermissionPolicy,
        untrusted:        UntrustedTracker,
        analytics_writer: AnalyticsWriter | None,
        hooks:            HookEngine | None        = None,
        settings:         Any                      = None,
        signals:          Counter[str] | None      = None,
        classifier:       ActionClassifier | None  = None,
    ) -> None:
        self._policy           = policy
        self._untrusted        = untrusted
        self._analytics_writer = analytics_writer
        self._hooks            = hooks
        self._settings         = settings
        self._signals          = signals if signals is not None else Counter()
        self._classifier       = classifier

    async def check(
        self,
        tool_use:    dict[str, Any],
        tool:        Any,
        parsed_args: Any,
        context:     ToolContext,
    ) -> ToolResult | None:
        """Apply the permission policy; ask the user when it says so. None means allowed."""
        perm_result = self._policy.decide(tool, tool.check_permissions(parsed_args, context), tool_use["input"])
        perm_result = self._untrusted.gate(tool, tool_use["input"], perm_result, self._policy.trust_level)
        if perm_result.behavior == PermissionBehavior.DENY:
            return self._denied(tool_use, f"Permission denied: {perm_result.message}", "policy")
        classifier = None if tool.is_read_only else self._classifier
        if perm_result.behavior == PermissionBehavior.ASK:
            verdict = await self._classify(classifier, tool_use, context) if classifier is not None and classifier.mode == SHADOW else None
            return await self._ask(tool_use, tool, parsed_args, context, perm_result.message, verdict)
        if classifier is not None and not self._policy.allowed_by_rule(tool_use["name"], tool_use["input"]):
            verdict = await self._classify(classifier, tool_use, context)
            return await self._apply_verdict(classifier, tool_use, tool, parsed_args, context, verdict)
        return None

    async def _ask(
        self,
        tool_use:    dict[str, Any],
        tool:        Any,
        parsed_args: Any,
        context:     ToolContext,
        message:     str,
        verdict:     Classification | None,
    ) -> ToolResult | None:
        """Put the question to the user, record the answer (and the classifier's verdict beside it), refuse on a no."""
        granted = await ask_user_permission(
            context   = context,
            tool_name = tool_use["name"],
            message   = self._with_preview(tool, parsed_args, context, message),
        )
        self._record_answer(tool_use, granted)
        self._record_verdict(tool_use, verdict, "allow_user" if granted else "deny_user")
        return None if granted else self._denied(tool_use, f"Permission denied by user: {message}", "user")

    async def _apply_verdict(
        self,
        classifier:  ActionClassifier,
        tool_use:    dict[str, Any],
        tool:        Any,
        parsed_args: Any,
        context:     ToolContext,
        verdict:     Classification,
    ) -> ToolResult | None:
        """What the classifier's verdict does to a call the policy allowed: nothing in shadow mode, a change in enforce mode."""
        if classifier.mode != ENFORCE or verdict.verdict == ALLOW:
            self._record_verdict(tool_use, verdict, "allow_auto")
            return None
        if verdict.verdict == DENY:
            self._record_verdict(tool_use, verdict, "deny_classifier")
            return self._denied(tool_use, f"Permission denied: the action classifier refused this call: {verdict.reason}", "classifier")
        return await self._ask(tool_use, tool, parsed_args, context, f"Action classifier: {verdict.reason}", verdict)

    async def _classify(self, classifier: ActionClassifier, tool_use: dict[str, Any], context: ToolContext) -> Classification:
        """Have the classifier judge the call, and count what it said."""
        verdict = await classifier.classify(tool_use["name"], tool_use["input"], context.cwd, context.state.get("classifier_feed"))
        if verdict.error:
            self._signals[CLASSIFIER_ERROR] += 1
        elif verdict.verdict == ASK:
            self._signals[CLASSIFIER_ASK] += 1
        elif verdict.verdict == DENY:
            self._signals[CLASSIFIER_DENY] += 1
        return verdict

    def _denied(self, tool_use: dict[str, Any], text: str, source: str) -> ToolResult:
        """The refusal for a call, with the retry hints PERMISSION_DENIED hooks gave for it."""
        hints: list[str] = []
        if self._hooks is not None:
            answers = self._hooks.emit(
                HookEvent.PERMISSION_DENIED, self._settings, tool_name=tool_use["name"], tool_input=tool_use["input"], source=source, reason=text,
            )
            hints = [answer.message for answer in answers if answer.message]
        note = "".join(f"\n\n[Retry hint from a hook: {hint}]" for hint in hints)
        return refusal(tool_use["id"], text + note)

    def _record_answer(self, tool_use: dict[str, Any], granted: bool) -> None:
        """Keep the user's answer so repeated approvals can be offered as rules (``nerdvana approvals``)."""
        if self._analytics_writer is None:
            return
        with contextlib.suppress(Exception):
            self._analytics_writer.record_approval(tool_use["name"], normalise(primary_argument(tool_use["name"], tool_use["input"])), granted)

    def _record_verdict(self, tool_use: dict[str, Any], verdict: Classification | None, outcome: str) -> None:
        """Keep the classifier's verdict next to what happened to the call (``nerdvana approvals`` compares them)."""
        if self._analytics_writer is None or verdict is None or self._classifier is None:
            return
        with contextlib.suppress(Exception):
            self._analytics_writer.record_classifier_verdict(
                tool_use["name"], normalise(primary_argument(tool_use["name"], tool_use["input"])),
                self._classifier.mode, "error" if verdict.error else verdict.verdict, verdict.reason, outcome,
            )

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
