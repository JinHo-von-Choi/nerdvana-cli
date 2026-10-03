"""Which model serves the agent loop: advice and escalation, recovery from a failed request, and the way back.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Iterator
from typing import TYPE_CHECKING, Any

from nerdvana_cli.core import signals
from nerdvana_cli.core.hooks import HookEvent
from nerdvana_cli.core.loop_hooks import hook_injection_messages
from nerdvana_cli.core.loop_state import LoopFlow, LoopTurn
from nerdvana_cli.core.phase_effort import IMPLEMENTATION
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

    An escalation lasts for the session; a fallback only for the prompt it served. With
    ``advisor.on_signals`` the advisor is asked once before an escalation, and the escalation only follows
    when the signal that called for help keeps coming.
    """

    def __init__(self, loop: AgentLoop) -> None:
        self._loop        = loop
        self._escalated   = False
        self._escalated_to: ModelState | None = None
        self._advised: dict[str, int] | None  = None   # the signal counts when the advisor was asked

    def model_state(self) -> ModelState:
        """The settings that name the model in use: provider, model, API key and base URL."""
        model = self._loop.settings.model
        return (model.provider, model.model, model.api_key, model.base_url)

    def begin_run(self) -> ModelState:
        """Start a run: the advisor's consultations and the implementation effort. Returns the model state to restore."""
        self._loop.advisor.start_run()
        self._loop.phase_effort.enter(IMPLEMENTATION)
        return self.model_state()

    def switch_model(self, provider: str | None, model: str) -> None:
        """Point the loop at *model*, on *provider* when one is given."""
        config = self._loop.settings.model
        if provider and provider != config.provider:
            config.provider = provider
            config.api_key  = resolve_api_key(ProviderName(provider))
            config.base_url = ""
        config.model       = model
        self._loop.provider = self._loop.create_provider_from_settings()
        self._loop.phase_effort.reapply()

    @contextlib.contextmanager
    def _announced(self, reason: str, provider: str | None, model: str) -> Iterator[None]:
        """Run a switch to *model* between its PRE_MODEL_SWITCH and POST_MODEL_SWITCH hooks."""
        config  = self._loop.settings.model
        current = (config.provider, config.model)
        target  = (provider or config.provider, model)
        self._emit(HookEvent.PRE_MODEL_SWITCH, current, target, reason)
        yield
        self._emit(HookEvent.POST_MODEL_SWITCH, current, target, reason)

    def _emit(self, event: HookEvent, current: tuple[str, str], target: tuple[str, str], reason: str) -> None:
        """Tell hooks the model is about to change (or has changed) from *current* to *target*."""
        loop = self._loop
        loop.hooks.emit(event, loop.settings, from_provider=current[0], from_model=current[1], to_provider=target[0], to_model=target[1], reason=reason)

    def restore_model(self, saved: ModelState) -> None:
        """After a prompt, go back to the model it started on, or to the one the session escalated to.

        The provider is only rebuilt when the model changed during the prompt, so the effort changes it
        keeps for the conversation (see ``core/phase_effort.py``) carry over to the next prompt.
        """
        config  = self._loop.settings.model
        target  = self._escalated_to or saved
        current = (config.provider, config.model)
        moved   = self.model_state() != target
        changed = current != (target[0], target[1])
        if changed:
            self._emit(HookEvent.PRE_MODEL_SWITCH, current, (target[0], target[1]), "restore")
        config.provider, config.model, config.api_key, config.base_url = target
        self._escalated_to = None
        self._loop.phase_effort.restore()
        if moved:
            self._loop.provider = self._loop.create_provider_from_settings()
        if changed:
            self._emit(HookEvent.POST_MODEL_SWITCH, current, (target[0], target[1]), "restore")

    async def maybe_escalate(self) -> str:
        """When the run's signals reach their thresholds, ask the advisor or switch to ``session.escalation_model``.

        With ``advisor.on_signals`` the first time asks the advisor and puts its guidance in front of the model.
        Otherwise, and when the advisor cannot answer or a signal has kept coming after its advice, the model
        is switched, once per session. Returns the notice to show, or an empty string when nothing changed. The
        thinking blocks kept on earlier assistant messages belong to the model that wrote them, so they are
        dropped on a switch.
        """
        loop    = self._loop
        session = loop.settings.session
        asks    = loop.settings.advisor.on_signals and self._advised is None
        if self._escalated or not (session.escalation_model or asks):
            return ""
        reason = self._trigger()
        if not reason:
            return ""
        if asks:
            notice = await self._advise(reason)
            if notice:
                return notice
        if not session.escalation_model:
            return ""
        self._escalated = True
        provider, model = parse_fallback(session.escalation_model)
        if provider and provider != loop.settings.model.provider and not resolve_api_key(ProviderName(provider)):
            logger.warning("escalation to %s skipped: no credential for provider %s", session.escalation_model, provider)
            return ""
        loop._signals[signals.ESCALATED] += 1
        with self._announced("escalation", provider, model):
            self.switch_model(provider, model)
        self._escalated_to = self.model_state()
        for message in loop.state.messages:
            message.provider_blocks = []
        return f"\n[bold yellow][Escalating to {loop.settings.model.provider}:{model}: {reason}][/bold yellow]\n"

    def _trigger(self) -> str:
        """The signal that calls for help, as text: the first at its threshold; after advice, one that has grown since."""
        loop       = self._loop
        counts     = loop.signal_summary()
        thresholds = loop.settings.session.escalation_signals
        if self._advised is not None:
            thresholds = {name: limit for name, limit in thresholds.items() if counts.get(name, 0) > self._advised.get(name, 0)}
        return signals.escalation_reason(counts, thresholds)

    async def _advise(self, reason: str) -> str:
        """Ask the advisor about *reason* and give its guidance to the model; the notice to show, or empty when it could not."""
        loop   = self._loop
        advice = await loop.advisor.advise(f"The run shows trouble ({reason}). What should be done differently?", reason)
        if not advice.ok:
            logger.info("the advisor was not consulted about %s: %s", reason, advice.text)
            return ""
        self._advised = loop.signal_summary()
        loop.state.messages.append(Message(role=Role.USER, content=f"[Advisor guidance, asked because of {reason}]\n{advice.text}"))
        return f"\n[bold yellow][Asked the advisor ({loop.settings.advisor.model}): {reason}][/bold yellow]\n"

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
            with self._announced("fallback", action.provider, action.model):
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
