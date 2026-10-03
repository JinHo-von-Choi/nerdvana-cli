"""Which model serves the agent loop: escalation, recovery from a failed request, and the way back.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from nerdvana_cli.core import signals
from nerdvana_cli.core.loop_hooks import hook_injection_messages
from nerdvana_cli.core.loop_state import LoopFlow, LoopTurn
from nerdvana_cli.core.provider_recovery import (
    COMPACT,
    FALLBACK,
    RESEND,
    RETRY,
    ProviderCallError,
    RecoveryPlanner,
    parse_fallback,
)
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.core.tool_ids import repair_tool_ids
from nerdvana_cli.providers.base import ProviderName
from nerdvana_cli.providers.errors import classify_exception
from nerdvana_cli.providers.factory import resolve_api_key
from nerdvana_cli.types import Message, Role

if TYPE_CHECKING:
    from nerdvana_cli.core.agent_loop import AgentLoop

logger = logging.getLogger(__name__)

ModelState = tuple[str, str, str, str]

# Requests a non-streaming resend may make before it gives up, tool rounds included.
_MAX_RESEND_ROUNDS = 10


class ModelFailover:
    """Switches the model of *loop*: once to ``session.escalation_model``, and per prompt after a failure.

    An escalation lasts for the session; a fallback only for the prompt it served.
    """

    def __init__(self, loop: AgentLoop) -> None:
        self._loop        = loop
        self._escalated   = False
        self._escalated_to: ModelState | None = None

    def model_state(self) -> ModelState:
        """The settings that name the model in use: provider, model, API key and base URL."""
        model = self._loop.settings.model
        return (model.provider, model.model, model.api_key, model.base_url)

    def switch_model(self, provider: str | None, model: str) -> None:
        """Point the loop at *model*, on *provider* when one is given."""
        config = self._loop.settings.model
        if provider and provider != config.provider:
            config.provider = provider
            config.api_key  = resolve_api_key(ProviderName(provider))
            config.base_url = ""
        config.model       = model
        self._loop.provider = self._loop.create_provider_from_settings()

    def restore_model(self, saved: ModelState) -> None:
        """After a prompt, go back to the model it started on, or to the one the session escalated to."""
        config = self._loop.settings.model
        config.provider, config.model, config.api_key, config.base_url = self._escalated_to or saved
        self._escalated_to  = None
        self._loop.provider = self._loop.create_provider_from_settings()

    def maybe_escalate(self) -> str:
        """Switch to ``session.escalation_model`` once, when the run's signals reach their thresholds.

        Returns the notice to show, or an empty string when nothing changed. The thinking blocks kept on
        earlier assistant messages belong to the model that wrote them, so they are dropped on a switch.
        """
        loop    = self._loop
        session = loop.settings.session
        if self._escalated or not session.escalation_model:
            return ""
        reason = signals.escalation_reason(loop.signal_summary(), session.escalation_signals)
        if not reason:
            return ""
        self._escalated = True
        provider, model = parse_fallback(session.escalation_model)
        if provider and provider != loop.settings.model.provider and not resolve_api_key(ProviderName(provider)):
            logger.warning("escalation to %s skipped: no credential for provider %s", session.escalation_model, provider)
            return ""
        loop._signals[signals.ESCALATED] += 1
        self.switch_model(provider, model)
        self._escalated_to = self.model_state()
        for message in loop.state.messages:
            message.provider_blocks = []
        return f"\n[bold yellow][Escalating to {loop.settings.model.provider}:{model}: {reason}][/bold yellow]\n"

    async def recover(
        self,
        exc:           Exception,
        recovery:      RecoveryPlanner,
        turn:          LoopTurn,
        flow:          LoopFlow,
        system_prompt: str,
        tools:         list[Any],
        tool_ctx:      ToolContext,
    ) -> AsyncGenerator[str, None]:
        """Retry, compact, fall back, resend without streaming, or end the run after a failed request."""
        loop       = self._loop
        from_event = isinstance(exc, ProviderCallError)
        failure    = exc.failure if isinstance(exc, ProviderCallError) else classify_exception(exc)
        action     = recovery.plan(
            failure,
            current          = loop.settings.model.model,
            current_provider = loop.settings.model.provider or "",
            streamed         = bool(turn.asst_text or turn.tool_uses),
        )
        if action.kind == RESEND:
            flow.finished = True
            yield "\n[dim yellow]Streaming error, retrying without streaming...[/dim yellow]\n"
            async for c in self.send_without_streaming(system_prompt, tools, tool_ctx):
                yield c
            return
        if action.kind == RETRY:
            loop._signals[signals.PROVIDER_RETRY] += 1
            yield f"\n[dim yellow][Retrying in {action.delay:.1f}s: {failure.kind}][/dim yellow]\n"
            await asyncio.sleep(action.delay)
            return
        if action.kind == COMPACT:
            cur_toks = flow.context_tokens
            async for status in loop._maybe_compact_messages(cur_toks, int(cur_toks * 0.6)):
                yield status
            return
        if action.kind == FALLBACK:
            loop._signals[signals.PROVIDER_FALLBACK] += 1
            self.switch_model(action.provider, action.model)
            yield f"\n[dim yellow][Fallback: {loop.settings.model.provider}:{action.model}][/dim yellow]\n"
            return
        loop.last_stop = "provider_error"
        flow.finished  = True
        if from_event:
            yield f"\n[bold red]Provider error: {exc}[/bold red]"
            return
        yield f"\n[bold red]Error: {exc}[/bold red]"
        loop.state.messages.append(Message(role=Role.ASSISTANT, content=f"Error occurred: {exc}"))

    async def send_without_streaming(self, system_prompt: str, tools: list[Any], context: ToolContext) -> AsyncGenerator[str, None]:
        """Finish the prompt with plain requests when the provider's stream fails, running tool calls in between."""
        loop = self._loop
        for _ in range(_MAX_RESEND_ROUNDS):
            loop._fire_before_api_call(tools)
            repair_tool_ids(loop.state.messages)
            try:
                result = await loop.provider.send(system_prompt, loop._to_provider_messages(), loop._declared(tools))
            except Exception as e:
                yield f"\n[bold red]Fallback error: {e}[/bold red]"
                return

            content   = result.get("content", "")
            tool_uses = result.get("tool_uses", [])
            usage     = result.get("usage", {})
            blocks    = result.get("provider_blocks")
            if content:
                yield content
            if usage:
                loop._apply_usage(usage, None)
            if tool_uses:
                loop.state.messages.append(Message(
                    role=Role.ASSISTANT, content=content if content else "[tool execution]", tool_uses=tool_uses,
                    provider_blocks=list(blocks or []),
                ))
                loop.session.record_assistant_message(content, tool_uses, blocks)
                for tr in await loop.tool_executor.run_batch(tool_uses, context):
                    loop.state.messages.append(Message(role=Role.TOOL, content=tr.content, tool_use_id=tr.tool_use_id, is_error=tr.is_error))
                loop.state.messages.extend(hook_injection_messages(loop.tool_executor))
                continue
            if content:
                loop.state.messages.append(Message(role=Role.ASSISTANT, content=content, provider_blocks=list(blocks or [])))
                loop.session.record_assistant_message(content, provider_blocks=blocks)
            return
