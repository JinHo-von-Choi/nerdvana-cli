"""Sub-agent concurrency bound and repeated-call detection.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nerdvana_cli.core.concurrency import RepeatDetector, agent_slot
from nerdvana_cli.core.hooks import HookEngine
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.core.tool import BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.core.tool_executor import ToolExecutor
from nerdvana_cli.types import ToolResult


def test_repeat_detector_counts_consecutive_identical_calls() -> None:
    detector = RepeatDetector()
    assert [detector.observe("Grep", {"p": "x"}) for _ in range(3)] == [1, 2, 3]
    assert detector.observe("Grep", {"p": "y"}) == 1
    assert detector.observe("Grep", {"p": "y"}) == 2


def test_argument_order_does_not_matter() -> None:
    detector = RepeatDetector()
    detector.observe("Grep", {"a": 1, "b": 2})
    assert detector.observe("Grep", {"b": 2, "a": 1}) == 2


def test_exempt_tools_never_count_or_break_a_run() -> None:
    detector = RepeatDetector(exempt=frozenset({"TaskGet"}))
    assert detector.observe("TaskGet", {"task_id": "t"}) == 0
    assert detector.observe("TaskGet", {"task_id": "t"}) == 0


async def test_agent_slot_bounds_concurrency_per_provider() -> None:
    active  = 0
    peak    = 0

    async def worker() -> None:
        nonlocal active, peak
        async with agent_slot("bound-test", limit=2):
            active += 1
            peak    = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(worker() for _ in range(6)))
    assert peak == 2


async def test_cancelled_holder_releases_its_slot() -> None:
    slot = agent_slot("cancel-test", limit=1)

    async def holder() -> None:
        async with slot:
            await asyncio.sleep(10)

    task = asyncio.create_task(holder())
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.wait_for(slot.acquire(), timeout=0.5)
    slot.release()


class _Counter(BaseTool[Any]):
    name             = "Probe"
    description_text = "probe"

    def __init__(self) -> None:
        self.calls = 0

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        self.calls += 1
        return ToolResult(tool_use_id="", content="same answer")


async def test_executor_warns_then_refuses_identical_repeats(tmp_path: Path) -> None:
    tool     = _Counter()
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(registry=registry, hooks=HookEngine(), settings=NerdvanaSettings())
    context  = ToolContext(cwd=str(tmp_path))

    results = []
    for index in range(5):
        batch = await executor.run_batch([{"id": f"c{index}", "name": "Probe", "input": {"q": 1}}], context)
        results.append(batch[0])

    assert "Note" not in results[1].content
    assert "Note" in results[2].content
    assert results[4].is_error
    assert "Refused" in results[4].content
    assert tool.calls == 4
