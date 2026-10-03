"""Token-based truncation of tool results with a saved full copy.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.hooks.hooks import HookEngine
from nerdvana_cli.core.token_estimator import approx_tokens
from nerdvana_cli.core.tool import TOOL_OUTPUT_DIR, BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.core.tool_executor import ToolExecutor
from nerdvana_cli.tools.web_tools import WebFetchTool
from nerdvana_cli.types import ToolResult


class _Big(BaseTool[Any]):
    name              = "Big"
    description_text  = "returns a lot"
    max_result_tokens = 1_000

    def __init__(self, body: str = "") -> None:
        self.body = body

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content=self.body)


def test_approx_tokens_counts_non_latin_text_per_character() -> None:
    assert approx_tokens("abcd" * 10) == 10
    assert approx_tokens("한국어") == 3


def test_small_result_is_untouched() -> None:
    assert _Big().truncate_result("short") == "short"


def test_large_result_keeps_head_and_tail_within_budget() -> None:
    body   = "HEAD-" + "x" * 20_000 + "-TAIL"
    result = _Big().truncate_result(body)
    assert result.startswith("HEAD-")
    assert result.endswith("-TAIL")
    assert "truncated" in result
    assert approx_tokens(result) <= 1_000


def test_truncation_is_stable_when_applied_twice() -> None:
    tool = _Big()
    once = tool.truncate_result("y" * 50_000)
    assert tool.truncate_result(once) == once


def test_non_latin_output_is_bounded_too() -> None:
    result = _Big().truncate_result("가" * 5_000)
    assert approx_tokens(result) <= 1_000


def test_full_output_is_saved_when_a_directory_is_set(tmp_path: Path) -> None:
    body  = "z" * 20_000
    token = TOOL_OUTPUT_DIR.set(str(tmp_path / "out"))
    try:
        result = _Big().truncate_result(body)
    finally:
        TOOL_OUTPUT_DIR.reset(token)
    saved = list((tmp_path / "out").iterdir())
    assert len(saved) == 1
    assert saved[0].read_text(encoding="utf-8") == body
    assert str(saved[0]) in result


def test_no_copy_is_written_outside_an_executor_call(tmp_path: Path) -> None:
    result = _Big().truncate_result("z" * 20_000)
    assert "saved to" not in result


def test_web_fetch_has_a_tighter_cap() -> None:
    assert WebFetchTool.max_result_tokens < BaseTool.max_result_tokens


async def test_executor_saves_into_the_session_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    registry = ToolRegistry()
    registry.register(_Big("w" * 20_000))
    executor = ToolExecutor(registry=registry, hooks=HookEngine(), settings=NerdvanaSettings())
    context  = ToolContext(cwd=str(tmp_path))
    context.state["session_id"] = "s-77"

    results = await executor.run_batch([{"id": "c1", "name": "Big", "input": {}}], context)

    saved = list((tmp_path / "data" / "tool-output" / "s-77").iterdir())
    assert len(saved) == 1
    assert str(saved[0]) in results[0].content
    assert TOOL_OUTPUT_DIR.get() is None
