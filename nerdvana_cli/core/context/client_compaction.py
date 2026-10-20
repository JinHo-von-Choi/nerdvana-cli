"""Compacting the history on the client, the fallback for the provider-side compaction.

Author: 최진호
Date:   2026-10-21

The history is compressed when the context passes the threshold: the model is asked
for a summary, and the history becomes that summary plus the last few messages. When
the model cannot summarize — the circuit is open, or the request failed — the history
is cut to the threshold instead.

The compaction is bracketed in the session transcript: ``compaction/start`` opens the
bracket before the work begins, ``compaction/summary`` records what the summary
replaced, and ``compaction/end`` closes it. A run that dies between the two leaves the
bracket open, and the next reader of the transcript sees the orphan.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from nerdvana_cli.core.context.compact import (
    ai_compact,
    compact_messages,
    drop_orphan_tool_results,
)
from nerdvana_cli.core.context.loop_context import COMPACT_STATUS_PREFIX, open_todos_note
from nerdvana_cli.core.state import signals
from nerdvana_cli.types import Role

if TYPE_CHECKING:
    from nerdvana_cli.core.loop.agent_loop import AgentLoop


class ClientCompaction:
    """Compresses the message history of a run when its context passes the threshold."""

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop

    async def run(self, cur_toks: int, thr: int) -> AsyncGenerator[str, None]:
        """Compress the history; yields the status strings the front ends read.

        The history is rewritten in place: the summary and the last few messages
        replace what came before.
        """
        loop   = self._loop
        before = len(loop.state.messages)
        loop.rewinder.marks.clear()   # compaction rewrites the history the marks point into
        loop._signals[signals.COMPACTION] += 1
        with loop.session.compaction(cur_toks, before):
            if not loop._compaction_state.is_circuit_open:
                yield f"{COMPACT_STATUS_PREFIX}compressing ({cur_toks} tokens)..."
                summary = await ai_compact(
                    loop.state.messages, loop.provider, loop._compaction_state, prompt=loop._compact_prompt
                )
                if summary is not None:
                    recent = loop.state.messages[-4:]
                    while recent and recent[0].role == Role.TOOL:
                        recent = recent[1:]
                    loop.state.messages = [summary] + recent
                    loop.session.record_compaction(cur_toks, before, "ai", before - len(loop.state.messages))
                    loop._context_budget.reset()
                    loop.state.messages.extend(open_todos_note(loop.session.session_id))
                    yield f"{COMPACT_STATUS_PREFIX}done"
                    return
            loop.state.messages = drop_orphan_tool_results(
                compact_messages(loop.state.messages, thr, loop._context_budget.estimator)
            )
            loop.session.record_compaction(cur_toks, before, "naive", before - len(loop.state.messages))
            loop._context_budget.reset()
            loop.state.messages.extend(open_todos_note(loop.session.session_id))
