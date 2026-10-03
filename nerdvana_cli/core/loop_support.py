"""What the agent loop adds around the permission classifier and compaction.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import functools
import logging
from collections.abc import AsyncGenerator, Callable
from dataclasses import replace
from typing import TYPE_CHECKING, TypeVar, cast

from nerdvana_cli.core.classifier import ClassifierFeed, Completion
from nerdvana_cli.core.hooks.hooks import HookEvent

if TYPE_CHECKING:
    from nerdvana_cli.core.agent_loop import AgentLoop

logger = logging.getLogger(__name__)

_CompactT = TypeVar("_CompactT", bound=Callable[..., AsyncGenerator[str, None]])


def classifier_feed(loop: AgentLoop) -> ClassifierFeed:
    """What the action classifier may use of *loop*: the user's own words, and a way to book its requests."""

    def charge(answer: Completion) -> float:
        origin = replace(loop.origin, agent_type="classifier", turn=loop.turns_used, last_tool=loop._last_tool)
        return loop.limits.record_auxiliary(answer.usage, origin, answer.provider, answer.model)

    return ClassifierFeed(lambda: loop.session.user_prompts, charge, lambda: bool(loop.limits.over_cost_limit()))


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
