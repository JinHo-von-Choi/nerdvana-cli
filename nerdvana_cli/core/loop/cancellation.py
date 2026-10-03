"""Structured cancellation: stop what a run is doing right now, down to the child process and the network call.

Author: 최진호
Date:   2026-10-03

A run is one asyncio task tree. Cancelling the task that drives it reaches a provider stream, an MCP call or a
web request at its next ``await``; a shell command needs more, because the process it started goes on running
when the call that waits for it is cancelled. ``race_abort`` turns an abort event into a cancellation of the
work, ``until_interrupted`` does the same for an event stream, and ``stop_process_group`` ends a command and
everything it started: it asks first (SIGTERM) and insists (SIGKILL) once the grace period has passed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# How long a command may take to end after SIGTERM before its process group is killed.
CANCEL_GRACE_SECONDS = 5.0
# Extra time cancelled work gets, beyond the grace period, to finish its own clean-up.
_SETTLE_SECONDS = 2.0
_POLL_SECONDS   = 0.02


@dataclass(frozen=True)
class Raced(Generic[T]):
    """How a race between some work and an abort event ended."""

    aborted: bool
    value:   T | None = None


def _quiet(task: asyncio.Future[Any]) -> None:
    """Mark the outcome of a finished task as seen, so a task nobody awaits again does not warn."""
    if not task.cancelled():
        task.exception()


async def cancel_and_wait(task: asyncio.Future[Any], grace: float | None = None) -> None:
    """Cancel *task* and give it the grace period, plus a margin, to finish; one that does not is left cancelled."""
    if not task.done():
        task.cancel()
        wait = (CANCEL_GRACE_SECONDS if grace is None else grace) + _SETTLE_SECONDS
        await asyncio.wait({task}, timeout=wait)
        if not task.done():
            logger.warning("cancelled work did not stop within %.1fs", wait)
    if task.done():
        _quiet(task)
    else:
        task.add_done_callback(_quiet)


async def race_abort(work: Awaitable[T], abort: asyncio.Event, grace: float | None = None, patience: float = 0.0) -> Raced[T]:
    """Await *work* until *abort* is set; then cancel it and report it aborted.

    The work runs as a task of its own, so setting the event stops it wherever it waits. With *patience*, work
    that is already running when the event is set gets that many seconds to finish by itself first. An
    exception of the work is raised as usual, and cancelling the caller cancels the work too.
    """
    task   = asyncio.ensure_future(work)
    waiter = asyncio.ensure_future(abort.wait())
    try:
        await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if not task.done() and patience > 0:
            await asyncio.wait({task}, timeout=patience)
    except asyncio.CancelledError:
        await cancel_and_wait(task, grace)
        raise
    finally:
        waiter.cancel()
    if task.done():
        return Raced(False, task.result())
    await cancel_and_wait(task, grace)
    return Raced(True)


async def until_interrupted(source: AsyncIterator[T], interrupt: asyncio.Event) -> AsyncIterator[T]:
    """Yield from *source* until *interrupt* is set, then cancel whatever the source is waiting on and stop.

    The source runs in a task of its own so a read that has not returned can be cancelled. Its exceptions are
    raised here, and the task is cancelled on every way out.
    """
    items: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def _pump() -> None:
        try:
            async for item in source:
                items.put_nowait(("item", item))
            items.put_nowait(("end", None))
        except Exception as exc:  # noqa: BLE001
            items.put_nowait(("error", exc))

    pump = asyncio.ensure_future(_pump())
    stop = asyncio.ensure_future(interrupt.wait())
    try:
        while not interrupt.is_set():
            if items.empty():
                getter = asyncio.ensure_future(items.get())
                await asyncio.wait({getter, stop}, return_when=asyncio.FIRST_COMPLETED)
                if not getter.done():
                    getter.cancel()
                    return
                kind, value = getter.result()
            else:
                kind, value = items.get_nowait()
            if kind == "end":
                return
            if kind == "error":
                raise value
            yield value
    finally:
        stop.cancel()
        await cancel_and_wait(pump)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _signal_group(pgid: int, sent: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, sent)


async def stop_process_group(process: asyncio.subprocess.Process, grace: float | None = None) -> None:
    """End *process* and everything in its process group: SIGTERM, then SIGKILL once *grace* seconds have passed.

    The process must lead a group of its own (started with ``start_new_session=True``). Returns when the
    group is gone and the process has been reaped.
    """
    pgid     = process.pid
    loop     = asyncio.get_running_loop()
    deadline = loop.time() + (CANCEL_GRACE_SECONDS if grace is None else grace)
    _signal_group(pgid, signal.SIGTERM)
    while _group_alive(pgid) and loop.time() < deadline:
        await asyncio.sleep(_POLL_SECONDS)
    if _group_alive(pgid):
        _signal_group(pgid, signal.SIGKILL)
    await process.wait()
