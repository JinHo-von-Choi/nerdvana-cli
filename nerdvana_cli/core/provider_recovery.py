"""Recovery decisions after a failed provider call.

Author: 최진호
Date:   2026-10-03

Given a classified failure, the planner answers one question: retry the same
model after a delay, switch to the next fallback, compact the history and try
again, resend without streaming, or give up. A call that already streamed
content or requested tools is never repeated, because repeating it would
duplicate visible output or side effects.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from nerdvana_cli.providers.base import ProviderName
from nerdvana_cli.providers.errors import AUTH, CONTEXT_LIMIT, DECODE, RETRYABLE, ProviderFailure

RETRY    = "retry"
FALLBACK = "fallback"
COMPACT  = "compact"
RESEND   = "resend"
GIVE_UP  = "give_up"

_PROVIDER_NAMES: frozenset[str] = frozenset(p.value for p in ProviderName)


class ProviderCallError(Exception):
    """A provider reported failure through an error event rather than raising."""

    def __init__(self, message: str, failure: ProviderFailure) -> None:
        super().__init__(message)
        self.failure = failure


def parse_fallback(entry: str) -> tuple[str | None, str]:
    """Split a fallback entry into (provider, model).

    ``provider:model`` names another provider; anything else is a model on the
    current provider. Model names may contain colons (``llama3:8b``), so the
    prefix only counts when it is a known provider name.
    """
    head, sep, tail = entry.partition(":")
    if sep and head in _PROVIDER_NAMES and tail:
        return head, tail
    return None, entry


@dataclass(frozen=True)
class RecoveryAction:
    """What the agent loop should do next."""

    kind:     str
    delay:    float      = 0.0
    provider: str | None = None
    model:    str        = ""


@dataclass
class RecoveryPlanner:
    """Per-run recovery state: retry counts, fallback position, cooldowns."""

    fallbacks:     list[str]
    max_retries:   int   = 2
    base_delay:    float = 1.0
    max_delay:     float = 30.0
    cooldown:      float = 60.0
    retries:       int   = 0
    compacted:     bool  = False
    _cooling:      dict[str, float] = field(default_factory=dict)

    def plan(self, failure: ProviderFailure, current: str, current_provider: str, streamed: bool) -> RecoveryAction:
        """Choose the next action for *failure* on model *current*."""
        if streamed:
            return RecoveryAction(GIVE_UP)
        if failure.kind == DECODE:
            return RecoveryAction(RESEND)
        if failure.kind == CONTEXT_LIMIT:
            if self.compacted:
                return RecoveryAction(GIVE_UP)
            self.compacted = True
            return RecoveryAction(COMPACT)
        if failure.kind == RETRYABLE:
            if self.retries < self.max_retries:
                self.retries += 1
                return RecoveryAction(RETRY, delay=self._delay(failure))
            return self._fallback(current, current_provider, require_other_provider=False)
        if failure.kind == AUTH:
            return self._fallback(current, current_provider, require_other_provider=True)
        return RecoveryAction(GIVE_UP)

    def _delay(self, failure: ProviderFailure) -> float:
        if failure.retry_after is not None:
            return min(failure.retry_after, self.max_delay)
        backoff = self.base_delay * (1 << max(self.retries - 1, 0))
        return min(backoff + random.uniform(0, self.base_delay), self.max_delay)

    def _fallback(self, current: str, current_provider: str, require_other_provider: bool) -> RecoveryAction:
        now = time.monotonic()
        self._cooling[f"{current_provider}:{current}"] = now
        for entry in self.fallbacks:
            provider, model = parse_fallback(entry)
            target          = provider or current_provider
            key             = f"{target}:{model}"
            if key == f"{current_provider}:{current}":
                continue
            if require_other_provider and target == current_provider:
                continue
            cooled_at = self._cooling.get(key)
            if cooled_at is not None and now - cooled_at < self.cooldown:
                continue
            self.retries = 0
            return RecoveryAction(FALLBACK, provider=provider, model=model)
        return RecoveryAction(GIVE_UP)
