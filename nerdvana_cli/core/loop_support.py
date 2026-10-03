"""What the agent loop adds around compaction.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import functools
import logging
from collections.abc import AsyncGenerator, Callable
from typing import TYPE_CHECKING, TypeVar, cast

from nerdvana_cli.core.hooks import HookEvent

if TYPE_CHECKING:
    from nerdvana_cli.core.agent_loop import AgentLoop

logger = logging.getLogger(__name__)

_CompactT = TypeVar("_CompactT", bound=Callable[..., AsyncGenerator[str, None]])


def with_compaction_hooks(compact: _CompactT) -> _CompactT:
    """Wrap the loop's compaction: a PRE_COMPACT hook can refuse it, POST_COMPACT hooks hear what it did.

    A refused compaction leaves the history as it is and is noted in the log and the session transcript.
    """

    @functools.wraps(compact)
    async def wrapper(loop: AgentLoop, cur_toks: int, thr: int) -> AsyncGenerator[str, None]:
        for result in loop.hooks.emit(HookEvent.PRE_COMPACT, loop.settings, tokens=cur_toks, messages=len(loop.state.messages)):
            if not result.allow:
                reason = result.message or "a PRE_COMPACT hook refused"
                logger.warning("compaction skipped: %s", reason)
                loop.session.record_system("compaction_skipped", {"reason": reason})
                return
        before, ai_before = len(loop.state.messages), loop._compaction_state.total_compactions
        async for status in compact(loop, cur_toks, thr):
            yield status
        loop.hooks.emit(
            HookEvent.POST_COMPACT, loop.settings, tokens_before=cur_toks, messages_before=before, messages_after=len(loop.state.messages),
            strategy="ai" if loop._compaction_state.total_compactions > ai_before else "naive",
        )

    return cast(_CompactT, wrapper)
