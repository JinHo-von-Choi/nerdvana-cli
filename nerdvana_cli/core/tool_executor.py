"""Tool batch executor extracted from AgentLoop.

Handles scheduling (parallel read / serial write), permission checking,
input validation, hook firing, and result serialisation.
Extracted as part of Phase 0A (T-0A-04).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from nerdvana_cli.core.loop_state import LoopState
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
        state: LoopState,  # noqa: ARG002 — reserved for future per-state routing
        context: ToolContext,
    ) -> list[ToolResult]:
        """Execute *calls* and return results in the same order as *calls*.

        Unknown tools produce an error ToolResult inline — they do not raise.
        """
        serial_calls:     list[tuple[dict[str, Any], Any]] = []
        concurrent_calls: list[tuple[dict[str, Any], Any]] = []

        for call in calls:
            tool = self._registry.get(call["name"])
            if tool is None:
                # Produce inline error; will be appended later by caller
                serial_calls.append((call, None))
            elif tool.is_concurrency_safe:
                concurrent_calls.append((call, tool))
            else:
                serial_calls.append((call, tool))

        results: list[ToolResult] = []

        for call, tool in serial_calls:
            if tool is None:
                results.append(
                    ToolResult(
                        tool_use_id = call["id"],
                        content     = f"Unknown tool: {call['name']}",
                        is_error    = True,
                    )
                )
                continue
            result = await self._run_single(call, tool, context)
            results.append(result)
            self._record_reminder(call, result)

        if concurrent_calls:
            tasks = [
                self._run_single(call, tool, context)
                for call, tool in concurrent_calls
            ]
            concurrent_results = await asyncio.gather(*tasks)
            results.extend(concurrent_results)
            for (call, _), result in zip(concurrent_calls, concurrent_results, strict=False):
                self._record_reminder(call, result)

        return results

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
            return ToolResult(
                tool_use_id = tool_id,
                content     = (
                    f"Permission required (auto-denied in current mode): "
                    f"{perm_result.message}"
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

        # Pre-edit checkpoint (opt-in, silently skipped when unavailable)
        if (
            self._checkpoint_manager is not None
            and tool_use["name"] in self._EDIT_TOOL_NAMES
        ):
            with contextlib.suppress(Exception):
                self._checkpoint_manager.before_edit(tool_use["name"])

        import time
        from datetime import datetime, timezone

        start_ts  = datetime.now(timezone.utc).isoformat()
        t0        = time.perf_counter()
        exc_class: str | None = None
        success   = True

        try:
            result: ToolResult = await tool.call(parsed_args, context, can_use_tool=None)
            result.tool_use_id = tool_id
            result.content     = tool.truncate_result(result.content)
            if result.is_error:
                success = False
            return result
        except Exception as exc:  # noqa: BLE001
            success   = False
            exc_class = type(exc).__name__
            return ToolResult(
                tool_use_id = tool_id,
                content     = f"Tool execution error: {exc}",
                is_error    = True,
            )
        finally:
            if self._analytics_writer is not None:
                duration_ms = int((time.perf_counter() - t0) * 1000)
                with contextlib.suppress(Exception):
                    self._analytics_writer.record_tool_call(
                        tool_name   = tool_use["name"],
                        start_ts    = start_ts,
                        duration_ms = duration_ms,
                        success     = success,
                        error_class = exc_class,
                    )

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
