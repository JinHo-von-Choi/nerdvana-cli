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
import os
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nerdvana_cli.core import paths
from nerdvana_cli.core.concurrency import RepeatDetector
from nerdvana_cli.core.policy import PermissionPolicy
from nerdvana_cli.core.schema_check import validate_arguments
from nerdvana_cli.core.signals import classify_result
from nerdvana_cli.core.token_estimator import estimate_tokens
from nerdvana_cli.core.tool import TOOL_OUTPUT_DIR, ToolContext, ToolRegistry
from nerdvana_cli.types import PermissionBehavior, ToolResult

if TYPE_CHECKING:
    from nerdvana_cli.core.analytics import AnalyticsWriter
    from nerdvana_cli.core.hooks import HookEngine

logger = logging.getLogger(__name__)

# Post-edit diagnostics: per-request time limit and how many new errors to list.
_DIAGNOSTICS_TIMEOUT = 8.0
_MAX_REPORTED_ERRORS = 10

# Identical consecutive calls: warn at the first threshold, refuse at the second.
_REPEAT_WARN  = 3
_REPEAT_BLOCK = 5
# Status polling repeats legitimately and never counts as a repeat.
_POLLING_TOOLS: frozenset[str] = frozenset({"TaskGet"})


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
        "replace_symbol_body",
        "insert_before_symbol",
        "insert_after_symbol",
        "safe_delete_symbol",
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
        policy:             PermissionPolicy | None = None,
    ) -> None:
        self._registry            = registry
        self._hooks               = hooks
        self._settings            = settings
        self._reminder            = reminder
        self._checkpoint_manager  = checkpoint_manager
        self._analytics_writer    = analytics_writer
        self._policy              = policy or PermissionPolicy()
        self._repeats             = RepeatDetector(exempt=_POLLING_TOOLS)
        self.signals: Counter[str] = Counter()
        self._pending_injections: list[dict[str, Any]] = []

    def drain_injections(self) -> list[dict[str, Any]]:
        """Return and clear the messages AFTER_TOOL hooks asked to inject.

        They cannot be placed between a tool call and its result, so the caller
        appends them once the batch's tool results are in the history.
        """
        drained = self._pending_injections
        self._pending_injections = []
        return drained

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

    @staticmethod
    def _refusal(tool_id: str, content: str) -> ToolResult:
        """An error result that tells the model a call was not run, and why."""
        return ToolResult(tool_use_id=tool_id, content=content, is_error=True)

    async def _run_single(
        self,
        tool_use: dict[str, Any],
        tool:     Any,
        context:  ToolContext,
    ) -> ToolResult:
        """Run one call, and count what its result says about how the run is going."""
        result    = await self._run_checked(tool_use, tool, context)
        sandbox   = context.state.get("sandbox")
        confined  = tool_use["name"] == "Bash" and sandbox is not None and getattr(sandbox, "mode", "off") != "off"
        self.signals.update(classify_result(result.content, result.is_error, shell_confined=confined))
        return result

    async def _run_checked(
        self,
        tool_use: dict[str, Any],
        tool:     Any,
        context:  ToolContext,
    ) -> ToolResult:
        """Run one call: refuse it when a check fails, otherwise execute and record it."""
        index = context.state.get("tool_index")
        if index is not None and index.is_unloaded(tool_use["name"]):
            name = tool_use["name"]
            return self._refusal(tool_use["id"], f"Tool {name} is not loaded yet. Call ToolSearch with query 'select:{name}' first.")
        repeats = self._repeats.observe(tool_use["name"], tool_use["input"])
        parsed_args, refusal = self._check_input(tool_use, tool, repeats)
        if refusal is not None:
            return refusal
        refusal = await self._check_permission(tool_use, tool, parsed_args, context)
        if refusal is not None:
            return refusal
        refusal = self._check_hooks_and_validation(tool_use, tool, parsed_args, context)
        if refusal is None:
            refusal = self._check_edit_scope(tool_use, parsed_args, context)
        if refusal is not None:
            return refusal

        # Pre-edit checkpoint (opt-in, skipped when no manager is configured)
        if self._checkpoint_manager is not None and tool_use["name"] in self._EDIT_TOOL_NAMES and getattr(parsed_args, "apply", True):
            self._capture_checkpoint(tool_use["name"], parsed_args)

        return await self._execute(tool_use, tool, parsed_args, context, repeats)

    def _check_input(self, tool_use: dict[str, Any], tool: Any, repeats: int) -> tuple[Any, ToolResult | None]:
        """Refuse a call that repeats too often or whose arguments are malformed.

        Returns the parsed arguments, or a refusal when the call must not run.
        """
        tool_input = tool_use["input"]
        tool_id    = tool_use["id"]
        if repeats >= _REPEAT_BLOCK:
            return None, self._refusal(
                tool_id,
                f"Refused: {tool_use['name']} was called {repeats} times in a row with identical "
                "arguments. Repeating it will not change the outcome; change the approach, or ask "
                "the user with AskUser if you are stuck.",
            )
        problems = validate_arguments(
            tool.input_schema or {},
            tool_input,
            reject_unknown = getattr(tool, "reject_unknown_args", True),
        )
        if problems:
            return None, self._refusal(tool_id, f"Invalid tool input: {'; '.join(problems)}")
        try:
            return tool.parse_args(tool_input), None
        except (TypeError, ValueError) as exc:
            return None, self._refusal(tool_id, f"Invalid tool input: {exc}")

    async def _check_permission(
        self,
        tool_use:    dict[str, Any],
        tool:        Any,
        parsed_args: Any,
        context:     ToolContext,
    ) -> ToolResult | None:
        """Apply the permission policy; ask the user when it says so. None means allowed."""
        tool_id     = tool_use["id"]
        perm_result = self._policy.decide(tool, tool.check_permissions(parsed_args, context))
        if perm_result.behavior == PermissionBehavior.DENY:
            return self._refusal(tool_id, f"Permission denied: {perm_result.message}")
        if perm_result.behavior == PermissionBehavior.ASK:
            granted = await self._ask_user_permission(
                context   = context,
                tool_name = tool_use["name"],
                message   = self._with_preview(tool, parsed_args, context, perm_result.message),
            )
            if not granted:
                return self._refusal(tool_id, f"Permission denied by user: {perm_result.message}")
        return None

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

    def _check_hooks_and_validation(
        self,
        tool_use:    dict[str, Any],
        tool:        Any,
        parsed_args: Any,
        context:     ToolContext,
    ) -> ToolResult | None:
        """Let BEFORE_TOOL hooks and the tool's own validation veto the call. None means go ahead."""
        from nerdvana_cli.core.hooks import HookContext, HookEvent

        tool_id  = tool_use["id"]
        hook_ctx = HookContext(
            event      = HookEvent.BEFORE_TOOL,
            tool_name  = tool_use["name"],
            tool_input = tool_use["input"],
            settings   = self._settings,
        )
        for hr in self._hooks.fire(hook_ctx):
            if not hr.allow:
                return self._refusal(tool_id, f"Blocked by hook: {hr.message}")
        validation_error = tool.validate_input(parsed_args, context)
        if validation_error:
            return self._refusal(tool_id, f"Validation error: {validation_error}")
        return None

    def _check_edit_scope(self, tool_use: dict[str, Any], parsed_args: Any, context: ToolContext) -> ToolResult | None:
        """Refuse an edit outside ``sandbox.edit_scope``; None means allowed or no scope is set."""
        scope = context.state.get("edit_scope")
        if scope is None or tool_use["name"] not in self._EDIT_TOOL_NAMES or not getattr(parsed_args, "apply", True):
            return None
        root    = Path(context.cwd).resolve()
        allowed = [(root / entry).resolve() for entry in scope]
        for target in self._edit_targets(parsed_args):
            resolved = (root / target).resolve()
            if not any(resolved == base or base in resolved.parents for base in allowed):
                where = ", ".join(scope) if scope else "nowhere"
                return self._refusal(tool_use["id"], f"Outside this agent's edit scope ({where}): {target}")
        return None

    async def _execute(
        self,
        tool_use:    dict[str, Any],
        tool:        Any,
        parsed_args: Any,
        context:     ToolContext,
        repeats:     int,
    ) -> ToolResult:
        """Call the tool, shape its result (truncation, diagnostics, repeat note) and record the call."""
        import time
        from datetime import UTC, datetime

        tool_id   = tool_use["id"]
        start_ts  = datetime.now(UTC).isoformat()
        t0        = time.perf_counter()
        exc_class: str | None = None
        success   = True

        result_text = ""

        output_dir = paths.user_data_home() / "tool-output" / str(context.state.get("session_id") or "default")
        dir_token  = TOOL_OUTPUT_DIR.set(str(output_dir))
        edited     = self._diagnosable_edit(tool_use["name"], parsed_args, context)
        baseline   = await self._error_messages(edited) if edited else None
        try:
            result: ToolResult = await tool.call(parsed_args, context, can_use_tool=None)
            result.tool_use_id = tool_id
            result.content     = tool.truncate_result(result.content)
            if result.is_error:
                success = False
            elif edited and baseline is not None:
                result.content += await self._new_errors_note(edited, baseline)
            if repeats >= _REPEAT_WARN:
                result.content += (
                    f"\n\n[Note: this exact call has now been made {repeats} times in a row. "
                    f"It will be refused from the {_REPEAT_BLOCK}th repeat; try something different.]"
                )
            result_text = result.content
            return result
        except Exception as exc:  # noqa: BLE001
            success     = False
            exc_class   = type(exc).__name__
            result_text = f"Tool execution error: {exc}"
            return self._refusal(tool_id, result_text)
        finally:
            TOOL_OUTPUT_DIR.reset(dir_token)
            self._record_call(tool_use, start_ts, time.perf_counter() - t0, success, exc_class, result_text)

    def _record_call(
        self,
        tool_use:    dict[str, Any],
        start_ts:    str,
        seconds:     float,
        success:     bool,
        exc_class:   str | None,
        result_text: str,
    ) -> None:
        """Write the finished call to the analytics database, when one is configured."""
        if self._analytics_writer is None:
            return
        provider, model = self._model_identity()
        with contextlib.suppress(Exception):
            self._analytics_writer.record_tool_call(
                tool_name     = tool_use["name"],
                start_ts      = start_ts,
                duration_ms   = int(seconds * 1000),
                success       = success,
                error_class   = exc_class,
                provider      = provider,
                model         = model,
                # The call arguments were produced by the model, and the
                # result is fed back to it, so each side is priced the
                # way the provider bills it. The count stays local: a
                # finished tool must not wait on a counting API.
                input_tokens  = estimate_tokens(result_text),
                output_tokens = estimate_tokens(json.dumps(tool_use["input"], ensure_ascii=False, default=str)),
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

    async def _ask_user_permission(
        self,
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
          (DENY) without blocking — safe default prevents unattended approval.

        Returns True only when the user explicitly confirms with y/yes.
        """
        import sys

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

        Messages a hook asks to inject are queued for drain_injections().
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
            self._pending_injections.extend(hr.inject_messages)

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

    def _diagnosable_edit(self, tool_name: str, parsed_args: Any, context: ToolContext) -> str | None:
        """Absolute path of the file an edit will change, when diagnostics can check it."""
        if tool_name not in self._EDIT_TOOL_NAMES or self._lsp_client() is None:
            return None
        session = getattr(self._settings, "session", None)
        if not getattr(session, "post_edit_diagnostics", True):
            return None
        if not getattr(parsed_args, "apply", True):
            return None
        targets = self._edit_targets(parsed_args)
        return os.path.join(context.cwd, targets[0]) if targets else None

    def _lsp_client(self) -> Any | None:
        """The language-server client behind the registered diagnostics tool, if any."""
        tool = self._registry.get("lsp_diagnostics")
        return getattr(tool, "_client", None) if tool is not None else None

    async def _error_messages(self, path: str) -> list[dict[str, Any]] | None:
        """Error-level diagnostics for *path*, or None when they could not be read in time."""
        client = self._lsp_client()
        if client is None:
            return None
        try:
            diagnostics = await asyncio.wait_for(client.diagnostics(path), timeout=_DIAGNOSTICS_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            logger.debug("post-edit diagnostics skipped for %s: %s", path, exc)
            return None
        return [d for d in diagnostics if d.get("severity") == "error"]

    async def _new_errors_note(self, path: str, baseline: list[dict[str, Any]]) -> str:
        """Describe errors present after the edit that were not there before it."""
        after = await self._error_messages(path)
        if not after:
            return ""
        remaining = Counter(str(d.get("message", "")) for d in baseline)
        fresh: list[dict[str, Any]] = []
        for diagnostic in after:
            message = str(diagnostic.get("message", ""))
            if remaining[message] > 0:
                remaining[message] -= 1
            else:
                fresh.append(diagnostic)
        if not fresh:
            return ""
        lines = [f"- line {d.get('line', '?')}: {d.get('message', '')}" for d in fresh[:_MAX_REPORTED_ERRORS]]
        more  = len(fresh) - len(lines)
        tail  = f"\n- ... and {more} more" if more > 0 else ""
        return "\n\nNew errors reported by the language server after this edit:\n" + "\n".join(lines) + tail

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
