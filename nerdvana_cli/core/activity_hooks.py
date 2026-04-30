"""Built-in hook handlers that update AgentLoop.activity_state."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from nerdvana_cli.core.activity_state import summarize_tool_call
from nerdvana_cli.core.hooks import HookContext, HookEvent, HookResult

if TYPE_CHECKING:
    from nerdvana_cli.core.agent_loop import AgentLoop

_Handler = Callable[[HookContext], HookResult | None]


def make_before_api_call_handler(loop: AgentLoop) -> _Handler:
    def handler(ctx: HookContext) -> HookResult | None:
        provider_name = loop.settings.model.provider or "provider"
        loop._set_activity(
            phase      = "waiting_api",
            label      = f"Waiting for {provider_name}...",
            detail     = "",
            tool_name  = "",
            started_at = time.time(),
        )
        return None
    return handler


def make_before_tool_handler(loop: AgentLoop) -> _Handler:
    def handler(ctx: HookContext) -> HookResult | None:
        label, detail = summarize_tool_call(ctx.tool_name, ctx.tool_input or {})
        loop._set_activity(
            phase      = "tool_running",
            label      = label,
            detail     = detail,
            tool_name  = ctx.tool_name,
            started_at = time.time(),
        )
        return None
    return handler


def make_after_tool_handler(loop: AgentLoop) -> _Handler:
    def handler(ctx: HookContext) -> HookResult | None:
        loop._set_activity(
            phase     = "streaming",
            label     = f"Streaming from {loop.settings.model.model}",
            detail    = "",
            tool_name = "",
        )
        return None
    return handler


def make_after_api_call_handler(loop: AgentLoop) -> _Handler:
    def handler(ctx: HookContext) -> HookResult | None:
        if ctx.stop_reason in {"end_turn", "max_tokens"}:
            loop._set_activity(phase="idle", label="Ready", detail="", tool_name="")
        return None
    return handler


def register_activity_hooks(loop: AgentLoop) -> None:
    """Register the four activity-state hooks on the loop's hook bus."""
    loop.hooks.register(HookEvent.BEFORE_API_CALL, make_before_api_call_handler(loop))
    loop.hooks.register(HookEvent.BEFORE_TOOL,     make_before_tool_handler(loop))
    loop.hooks.register(HookEvent.AFTER_TOOL,      make_after_tool_handler(loop))
    loop.hooks.register(HookEvent.AFTER_API_CALL,  make_after_api_call_handler(loop))
