"""Time limits on a provider event stream.

Author: 최진호
Date:   2026-10-03

A stalled connection otherwise holds the agent loop forever. Two limits apply:
the gap between consecutive events (idle) and the whole response (total).
Either limit set to 0 or less is disabled.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import TypeVar

T = TypeVar("T")


class StreamTimeoutError(Exception):
    """The provider stream went silent or ran past its total budget."""


async def guarded_stream(source: AsyncIterator[T], idle: float, total: float) -> AsyncIterator[T]:
    """Yield from *source*, raising StreamTimeoutError when a limit is hit.

    The source is closed on every exit path, so the underlying HTTP stream is
    released even when the caller stops early or a limit fires.
    """
    iterator = source.__aiter__()
    deadline = time.monotonic() + total if total > 0 else None
    try:
        while True:
            waits: list[float] = []
            if idle > 0:
                waits.append(idle)
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StreamTimeoutError(f"provider response exceeded the {total:g}s timeout")
                waits.append(remaining)
            wait = min(waits) if waits else None
            try:
                item = await asyncio.wait_for(iterator.__anext__(), wait)
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                if deadline is not None and time.monotonic() >= deadline:
                    raise StreamTimeoutError(f"provider response exceeded the {total:g}s timeout") from exc
                raise StreamTimeoutError(f"provider stream idle timeout after {idle:g}s") from exc
            yield item
    finally:
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()
