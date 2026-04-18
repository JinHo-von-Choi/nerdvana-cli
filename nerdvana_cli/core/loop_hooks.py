"""Recovery hook engine for the agent loop.

Wraps the general HookEngine to provide typed on_api_call / on_tool_result /
on_turn_end entry points. Extracted from AgentLoop as part of Phase 0A
(T-0A-05).
"""

from __future__ import annotations

import re
from typing import Any

from nerdvana_cli.core.loop_state import LoopState  # noqa: TC001

_RETRYABLE_PATTERNS = re.compile(
    r"(429|529|503|timeout|rate.?limit|too many requests|service unavailable)",
    re.IGNORECASE,
)


class LoopHookEngine:
    """Typed hook dispatcher for AgentLoop lifecycle events.

    Each method receives the current LoopState, performs side-effects via
    the underlying HookEngine, and returns an (possibly updated) LoopState.
    """

    def __init__(self, hooks: Any, settings: Any, registry: Any) -> None:
        """Initialise with existing HookEngine, settings, and tool registry.

        Args:
            hooks:    A ``HookEngine`` instance with registered handlers.
            settings: ``NerdvanaSettings`` for the current session.
            registry: ``ToolRegistry`` used to enumerate available tools.
        """
        self._hooks    = hooks
        self._settings = settings
        self._registry = registry

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def on_api_call(
        self,
        state:    LoopState,
        response: Any,
        messages: list[Any],
    ) -> tuple[LoopState, list[Any]]:
        """Fire AFTER_API_CALL hooks and return (updated_state, inject_messages).

        Called after a provider streaming response completes.  The stop_reason
        embedded in *response* determines which hooks react.

        Args:
            state:    Current LoopState.
            response: A dict-like object with at least a ``stop_reason`` key.
            messages: Current conversation message list (for hook context).

        Returns:
            Tuple of (possibly evolved LoopState, list of messages to inject).
        """
        from nerdvana_cli.core.hooks import HookContext, HookEvent

        stop_reason = response.get("stop_reason") if isinstance(response, dict) else getattr(response, "stop_reason", None)
        hook_ctx = HookContext(
            event       = HookEvent.AFTER_API_CALL,
            settings    = self._settings,
            tools       = self._registry.all_tools(),
            messages    = messages,
            stop_reason = stop_reason,
            extra       = {"agent_loop": None},  # filled by caller if needed
        )
        inject: list[Any] = []
        for hr in self._hooks.fire(hook_ctx):
            inject.extend(hr.inject_messages)

        new_stop = stop_reason or state.stop_reason
        new_state = state.evolve(stop_reason=new_stop)
        return new_state, inject

    def on_tool_result(
        self,
        state:  LoopState,
        result: Any,
    ) -> LoopState:
        """Fire AFTER_TOOL hooks for a completed tool result.

        Args:
            state:  Current LoopState.
            result: A ToolResult instance.

        Returns:
            Possibly evolved LoopState.
        """
        from nerdvana_cli.core.hooks import HookContext, HookEvent

        hook_ctx = HookContext(
            event       = HookEvent.AFTER_TOOL,
            settings    = self._settings,
            tool_result = result,
        )
        self._hooks.fire(hook_ctx)
        return state

    def on_turn_end(self, state: LoopState) -> LoopState:
        """Called when the loop is about to return (end_turn path).

        Args:
            state: Current LoopState.

        Returns:
            Evolved LoopState with stop_reason="end_turn".
        """
        return state.evolve(stop_reason="end_turn")

    def _is_retryable_error(self, error: Exception) -> bool:
        """Return True if *error* should trigger a model fallback / retry."""
        return bool(_RETRYABLE_PATTERNS.search(str(error)))
