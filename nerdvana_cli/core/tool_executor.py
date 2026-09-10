"""Tool batch executor extracted from AgentLoop.

Handles scheduling (parallel read / serial write), permission checking,
input validation, hook firing, and result serialisation.
Extracted as part of Phase 0A (T-0A-04).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from nerdvana_cli.core.token_estimator import estimate_tokens
from nerdvana_cli.core.tool import ToolContext, ToolRegistry
from nerdvana_cli.types import PermissionBehavior, ToolResult

if TYPE_CHECKING:
    from nerdvana_cli.core.analytics import AnalyticsWriter
    from nerdvana_cli.core.hooks import HookEngine

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Executes a batch of tool calls with read-parallel / write-serial policy.

    Policy:
    - Tools where ``is_concurrency_safe`` is True are gathered concurrently.
    - All other tools run serially in declaration order.
    - BEFORE_TOOL / AFTER_TOOL hooks are fired via the provided HookEngine.
    """

    # Edit-tool names that trigger a checkpoint before execution
    _EDIT_TOOL_NAMES: frozenset[str] = frozenset({
        "FileEdit",
        "FileWrite",
        "ReplaceSymbolBody",
        "InsertBeforeSymbol",
        "InsertAfterSymbol",
        "SafeDeleteSymbol",
    })

    # Argument attributes that carry the file an edit tool is about to change,
    # in resolution order. File tools expose ``path``; symbol tools expose
    # ``relative_path``.
    _EDIT_PATH_ATTRS: tuple[str, ...] = ("path", "relative_path", "file_path")

    def __init__(
        self,
        registry:           ToolRegistry,
        hooks:              HookEngine,
        settings:           Any,
        reminder:           Any | None = None,
        checkpoint_manager: Any | None = None,
        analytics_writer:   AnalyticsWriter | None = None,
    ) -> None:
        self._registry            = registry
        self._hooks               = hooks
        self._settings            = settings
        self._reminder            = reminder
        self._checkpoint_manager  = checkpoint_manager
        self._analytics_writer    = analytics_writer

    async def run_batch(
        self,
        calls: list[dict[str, Any]],
        context: ToolContext,
    ) -> list[ToolResult]:
        """Execute *calls* and return results in the same order as *calls*.

        Unknown tools produce an error ToolResult inline — they do not raise.
        """
        # Each entry carries the position it came from, so scheduling a call
        # into the concurrent group cannot move its result in the reply.
        serial_calls:     list[tuple[int, dict[str, Any], Any]] = []
        concurrent_calls: list[tuple[int, dict[str, Any], Any]] = []

        for index, call in enumerate(calls):
            tool = self._registry.get(call["name"])
            if tool is None:
                # Produce inline error; will be appended later by caller
                serial_calls.append((index, call, None))
            elif tool.is_concurrency_safe:
                concurrent_calls.append((index, call, tool))
            else:
                serial_calls.append((index, call, tool))

        slots: list[ToolResult | None] = [None] * len(calls)

        for index, call, tool in serial_calls:
            if tool is None:
                slots[index] = ToolResult(
                    tool_use_id = call["id"],
                    content     = f"Unknown tool: {call['name']}",
                    is_error    = True,
                )
                continue
            result = await self._run_single(call, tool, context)
            slots[index] = result
            self._record_reminder(call, result)
            self._fire_after_tool(call, result)

        if concurrent_calls:
            tasks = [
                self._run_single(call, tool, context)
                for _, call, tool in concurrent_calls
            ]
            concurrent_results = await asyncio.gather(*tasks)
            for (index, call, _), result in zip(concurrent_calls, concurrent_results, strict=False):
                slots[index] = result
                self._record_reminder(call, result)
                self._fire_after_tool(call, result)

        return [result for result in slots if result is not None]

    async def _run_single(
        self,
        tool_use: dict[str, Any],
        tool:     Any,
        context:  ToolContext,
    ) -> ToolResult:
        """Execute a single tool with permission check, hook firing, and validation."""
        from nerdvana_cli.core.hooks import HookContext, HookEvent

        tool_input = tool_use["input"]
        tool_id    = tool_use["id"]

        try:
            parsed_args = tool.parse_args(tool_input)
        except (TypeError, ValueError) as exc:
            return ToolResult(
                tool_use_id = tool_id,
                content     = f"Invalid tool input: {exc}",
                is_error    = True,
            )

        perm_result = tool.check_permissions(parsed_args, context)
        if perm_result.behavior == PermissionBehavior.DENY:
            return ToolResult(
                tool_use_id = tool_id,
                content     = f"Permission denied: {perm_result.message}",
                is_error    = True,
            )
        if perm_result.behavior == PermissionBehavior.ASK:
            granted = await self._ask_user_permission(
                tool_name = tool_use["name"],
                message   = perm_result.message,
            )
            if not granted:
                return ToolResult(
                    tool_use_id = tool_id,
                    content     = (
                        f"Permission denied by user: {perm_result.message}"
                    ),
                    is_error    = True,
                )

        hook_ctx = HookContext(
            event      = HookEvent.BEFORE_TOOL,
            tool_name  = tool_use["name"],
            tool_input = tool_input,
            settings   = self._settings,
        )
        for hr in self._hooks.fire(hook_ctx):
            if not hr.allow:
                return ToolResult(
                    tool_use_id = tool_id,
                    content     = f"Blocked by hook: {hr.message}",
                    is_error    = True,
                )

        validation_error = tool.validate_input(parsed_args, context)
        if validation_error:
            return ToolResult(
                tool_use_id = tool_id,
                content     = f"Validation error: {validation_error}",
                is_error    = True,
            )

        # Pre-edit checkpoint (opt-in, skipped when no manager is configured)
        if (
            self._checkpoint_manager is not None
            and tool_use["name"] in self._EDIT_TOOL_NAMES
        ):
            self._capture_checkpoint(tool_use["name"], parsed_args)

        import time
        from datetime import UTC, datetime

        start_ts  = datetime.now(UTC).isoformat()
        t0        = time.perf_counter()
        exc_class: str | None = None
        success   = True

        result_text = ""

        try:
            result: ToolResult = await tool.call(parsed_args, context, can_use_tool=None)
            result.tool_use_id = tool_id
            result.content     = tool.truncate_result(result.content)
            if result.is_error:
                success = False
            result_text = result.content
            return result
        except Exception as exc:  # noqa: BLE001
            success     = False
            exc_class   = type(exc).__name__
            result_text = f"Tool execution error: {exc}"
            return ToolResult(
                tool_use_id = tool_id,
                content     = result_text,
                is_error    = True,
            )
        finally:
            if self._analytics_writer is not None:
                duration_ms        = int((time.perf_counter() - t0) * 1000)
                provider, model    = self._model_identity()
                with contextlib.suppress(Exception):
                    self._analytics_writer.record_tool_call(
                        tool_name     = tool_use["name"],
                        start_ts      = start_ts,
                        duration_ms   = duration_ms,
                        success       = success,
                        error_class   = exc_class,
                        provider      = provider,
                        model         = model,
                        # The call arguments were produced by the model, and the
                        # result is fed back to it, so each side is priced the
                        # way the provider bills it. The count stays local: a
                        # finished tool must not wait on a counting API.
                        input_tokens  = estimate_tokens(result_text),
                        output_tokens = estimate_tokens(json.dumps(tool_input, ensure_ascii=False, default=str)),
                    )

    def _model_identity(self) -> tuple[str | None, str | None]:
        """Provider and model names to attribute a tool call to.

        Either side is None when settings carry no usable string, which keeps a
        stand-in settings object from writing a placeholder into the price
        columns that the cost report reads.
        """
        model_cfg = getattr(self._settings, "model", None)
        provider  = getattr(model_cfg, "provider", None)
        model     = getattr(model_cfg, "model", None)
        return (
            provider if isinstance(provider, str) and provider else None,
            model    if isinstance(model,    str) and model    else None,
        )

    async def _ask_user_permission(self, tool_name: str, message: str) -> bool:
        """Prompt the user for explicit confirmation when a tool returns ASK.

        Behaviour:
        - Interactive TTY: prints a y/N prompt and reads a single line.
          Accepts "y" or "yes" (case-insensitive); everything else is DENY.
          An empty reply defaults to N (fail-safe).
        - Non-interactive (piped stdin / CI / batch): immediately returns False
          (DENY) without blocking — safe default prevents unattended approval.

        Returns True only when the user explicitly confirms with y/yes.
        """
        import sys

        prompt_text = (
            f"\n[permission] {tool_name}: {message}\n"
            "Allow this action? [y/N] "
        )

        if not sys.stdin.isatty():
            # Non-interactive session — fail-safe DENY, never block.
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
            # stdin closed unexpectedly — treat as DENY.
            granted = False

        logger.info(
            "ASK permission for %s: user replied %r → %s",
            tool_name,
            reply if "reply" in dir() else "<eof>",
            "ALLOW" if granted else "DENY",
        )
        return granted

    def _fire_after_tool(self, call: dict[str, Any], result: ToolResult) -> None:
        """Fire AFTER_TOOL hooks for a completed tool call.

        This path returns tool results, not conversation messages, so a hook
        that asks for an injection cannot be honoured here. That is reported
        rather than dropped in silence.
        """
        from nerdvana_cli.core.hooks import HookContext, HookEvent

        hook_ctx = HookContext(
            event       = HookEvent.AFTER_TOOL,
            settings    = self._settings,
            tool_name   = call["name"],
            tool_input  = call.get("input") or {},
            tool_result = result,
        )
        for hr in self._hooks.fire(hook_ctx):
            if hr.inject_messages:
                logger.warning(
                    "AFTER_TOOL hook requested %d message injection(s) after %s; "
                    "the tool execution path cannot deliver them",
                    len(hr.inject_messages),
                    call["name"],
                )

    def _capture_checkpoint(self, tool_name: str, parsed_args: Any) -> None:
        """Snapshot the files *tool_name* is about to change.

        Passing the edit targets is what arms undo: ``before_edit`` copies
        nothing when it receives no path. Every failure below is logged instead
        of suppressed, because an undo facility that captures nothing while
        appearing armed is the data-loss risk it exists to remove.
        """
        if self._checkpoint_manager is None:
            return

        try:
            if not getattr(parsed_args, "apply", True):
                # Preview-only symbol edit: nothing on disk changes.
                return
            targets = self._edit_targets(parsed_args)
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
            self._checkpoint_manager.before_edit(tool_name, targets)
        except Exception as exc:  # noqa: BLE001
            logger.warning("checkpoint: capture failed before %s: %s", tool_name, exc)

    def _edit_targets(self, parsed_args: Any) -> list[str]:
        """Return the file paths carried by a parsed edit-tool argument object."""
        for attr in self._EDIT_PATH_ATTRS:
            value = getattr(parsed_args, attr, None)
            if isinstance(value, str) and value.strip():
                return [value]
        return []

    def _record_reminder(self, call: dict[str, Any], result: ToolResult) -> None:
        """Record a completed tool call into the context reminder, if present."""
        if self._reminder is None:
            return
        from nerdvana_cli.core.context_reminder import RecentToolResult

        self._reminder.record_tool(
            RecentToolResult(
                name         = call["name"],
                args_summary = str(call.get("input", ""))[:100],
                preview      = (result.content or "")[:200],
                ok           = not result.is_error,
            )
        )
