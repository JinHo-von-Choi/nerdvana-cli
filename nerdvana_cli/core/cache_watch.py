"""Noticing a prompt-cache miss that nothing explains.

Author: 최진호
Date:   2026-10-03

A provider that caches the start of a request reports how many input tokens it read from the cache.
When that figure was positive on one request and falls to zero on the next, with the same model and
nothing in between that is known to change the start of the request, the cache was lost for a reason
nobody chose: a changed system prompt or tool list, an unstable serialization, an expired entry. Each
one costs the whole prompt at full price, so it is counted as the ``cache_miss`` signal and logged.

The start of a request legitimately changes when the history is compacted or masked, when the model is
switched (escalation or fallback), so those are told apart by the signal counts the loop already keeps.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from nerdvana_cli.core import signals

logger = logging.getLogger(__name__)

# Signals that count something that rewrites the start of the next request on purpose.
_PREFIX_CHANGING = (signals.COMPACTION, signals.OBSERVATIONS_MASKED, signals.ESCALATED, signals.PROVIDER_FALLBACK)


class CacheWatch:
    """Compares the cache read of each request with the one before it."""

    def __init__(self) -> None:
        self._last: tuple[str, int, int] | None = None   # model, epoch, cache read tokens

    def observe(self, model: str, usage: Mapping[str, int], counts: Mapping[str, int]) -> bool:
        """Record one request; True when its cache read fell to zero with no known cause."""
        epoch = sum(counts.get(name, 0) for name in _PREFIX_CHANGING)
        reads = usage.get("cache_read_tokens", 0)
        last, self._last = self._last, (model, epoch, reads)
        missed = last is not None and last[0] == model and last[1] == epoch and last[2] > 0 and reads == 0
        if missed:
            logger.warning("prompt cache miss on %s: %d cached tokens on the previous request, none on this one", model, last[2] if last else 0)
        return missed
