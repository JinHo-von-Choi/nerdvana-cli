"""Compacting the history on the provider's side, with the client-side compaction as the fallback.

Author: 최진호
Date:   2026-10-03

A provider that can summarize the conversation itself (``supports_server_compaction``, today Anthropic with
``model.anthropic_compaction: on``) is asked to when the context passes the compaction threshold. The reply is
a compaction block: it is appended to the history as an assistant message, recorded in the session transcript
like any assistant turn, and from then on the provider sends it in place of every message before it, while the
loop keeps its own history whole. A user message follows the block (the open todo note, else a short
continuation line), so the next request ends on a user message. Because the history is not rewritten, the
prompt marks that ``/rewind`` uses stay valid.

The request is only made between turns, from the loop's context step, where the history holds every tool
result its calls asked for; a context-limit failure in the middle of a request still goes to the client-side
compaction in ``core/compact.py``. Any failure of the request, or a reply without a summary, falls back to
that compaction for the same trigger. After ``session.compact_max_failures`` failures in a row the provider is
no longer asked in this session.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from nerdvana_cli.core import signals
from nerdvana_cli.core.loop_context import COMPACT_STATUS_PREFIX, open_todos_note
from nerdvana_cli.types import Message, Role

if TYPE_CHECKING:
    from nerdvana_cli.core.agent_loop import AgentLoop

logger = logging.getLogger(__name__)

# What follows the block when there is no open todo note: the request must end on a user message.
_CONTINUE = "[Context compacted by the provider: the summary above stands for the earlier conversation. Continue the work from it.]"


class ServerCompaction:
    """Asks the provider of *loop* to compact the history, or hands the job to the loop's own compaction."""

    def __init__(self, loop: AgentLoop) -> None:
        self._loop     = loop
        self._failures = 0

    def _eligible(self) -> bool:
        """True when the provider can compact, the setting is on and it has not failed too often."""
        loop = self._loop
        return (
            getattr(loop.provider, "supports_server_compaction", False) is True
            and loop.settings.model.anthropic_compaction == "on"
            and self._failures < loop.settings.session.compact_max_failures
        )

    async def run(self, tokens: int, threshold: int, system_prompt: str, tools: list[Any]) -> AsyncGenerator[str, None]:
        """Compact the history once the context passed *threshold*; yields the status strings the front ends read."""
        if self._eligible():
            yield f"{COMPACT_STATUS_PREFIX}compressing on the provider ({tokens} tokens)..."
            if await self._compact(tokens, system_prompt, tools):
                yield f"{COMPACT_STATUS_PREFIX}done"
                return
        async for status in self._loop._maybe_compact_messages(tokens, threshold):
            yield status

    async def _compact(self, tokens: int, system_prompt: str, tools: list[Any]) -> bool:
        """Ask the provider for the summary and append it to the history; False when it could not be had."""
        loop     = self._loop
        provider: Any = loop.provider   # only a provider with supports_server_compaction gets here
        before   = len(loop.state.messages)
        try:
            result = await provider.compact(system_prompt, loop._to_provider_messages(), loop._declared(tools))
        except Exception as exc:  # noqa: BLE001 - any failure falls back to the client-side compaction
            return self._failed(str(exc))
        blocks = result.get("provider_blocks")
        if result.get("is_error") or not blocks:
            return self._failed(str(result.get("content") or "no summary in the reply"))
        self._failures = 0
        loop._signals[signals.COMPACTION] += 1   # before the usage is recorded: the cache watch must not take the new start for a miss
        loop._apply_usage(result.get("usage") or {}, None)
        loop.state.messages.append(Message(role=Role.ASSISTANT, content="", provider_blocks=list(blocks)))
        loop.session.record_assistant_message("", provider_blocks=blocks)
        loop.session.record_compaction(tokens_before=tokens, messages_before=before, strategy="server")
        loop._context_budget.reset()
        loop.state.messages.extend(open_todos_note(loop.session.session_id) or [Message(role=Role.USER, content=_CONTINUE)])
        return True

    def _failed(self, reason: str) -> bool:
        self._failures += 1
        logger.warning("server-side compaction failed (%d in a row): %s", self._failures, reason)
        return False
