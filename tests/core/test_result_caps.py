"""Per-tool result size limits (``tools.max_result_chars``) on top of the head and tail truncation.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nerdvana_cli.core.hooks import HookEngine
from nerdvana_cli.core.settings import MIN_RESULT_CHARS, NerdvanaSettings, ToolsConfig
from nerdvana_cli.core.tool import TOOL_OUTPUT_DIR, TOOL_RESULT_CAP, BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.core.tool_executor import ToolExecutor
from nerdvana_cli.types import ToolResult


class _Big(BaseTool[Any]):
    description_text  = "returns a lot"
    max_result_tokens = 30_000

    def __init__(self, name: str, body: str) -> None:
        self.name = name
        self.body = body

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        # Like Bash and the search tools, the tool bounds its own output before the executor sees it.
        return ToolResult(tool_use_id="", content=self.truncate_result(self.body))


def _body(length: int) -> str:
    return "".join(f"line {n:06d} of the output\n" for n in range(length // 26 + 1))[:length]


# ---------------------------------------------------------------------------
# Which limit applies
# ---------------------------------------------------------------------------


def test_an_exact_name_beats_a_glob_and_a_longer_glob_beats_a_shorter_one() -> None:
    config = ToolsConfig(max_result_chars={"mcp__*": 4_000, "mcp__github__*": 6_000, "mcp__github__search": 2_000, "Bash": 9_000})
    assert config.result_cap("mcp__github__search") == 2_000
    assert config.result_cap("mcp__github__issues") == 6_000
    assert config.result_cap("mcp__jira__list") == 4_000
    assert config.result_cap("Bash") == 9_000
    assert config.result_cap("Grep") is None
    assert ToolsConfig().result_cap("Bash") is None


def test_a_limit_too_small_to_leave_anything_to_read_is_refused() -> None:
    with pytest.raises(ValueError, match="characters"):
        ToolsConfig(max_result_chars={"Bash": MIN_RESULT_CHARS - 1})
    assert ToolsConfig(max_result_chars={"Bash": MIN_RESULT_CHARS}).result_cap("Bash") == MIN_RESULT_CHARS


def test_the_setting_is_read_from_the_config_file_and_a_bad_entry_falls_back_with_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    config = tmp_path / "c.yml"
    config.write_text('tools:\n  max_result_chars:\n    Grep: 8000\n    "mcp__github__*": 4000\n', encoding="utf-8")
    loaded = NerdvanaSettings.load(str(config))
    assert loaded.tools.result_cap("Grep") == 8000 and loaded.tools.result_cap("mcp__github__x") == 4000
    config.write_text("tools:\n  max_result_chars:\n    Grep: 10\n", encoding="utf-8")
    loaded = NerdvanaSettings.load(str(config))
    assert loaded.tools.max_result_chars == {} and any(w.path == "tools.max_result_chars" for w in loaded.load_warnings)
    config.write_text("tools: 5\n", encoding="utf-8")
    assert NerdvanaSettings.load(str(config)).tools.max_result_chars == {}


# ---------------------------------------------------------------------------
# The truncation itself
# ---------------------------------------------------------------------------


def test_a_result_over_the_cap_keeps_head_and_tail_and_names_the_saved_file(tmp_path: Path) -> None:
    tool, body = _Big("Big", _body(50_000)), _body(50_000)
    dir_token  = TOOL_OUTPUT_DIR.set(str(tmp_path))
    cap_token  = TOOL_RESULT_CAP.set(5_000)
    try:
        cut = tool.truncate_result(body)
    finally:
        TOOL_RESULT_CAP.reset(cap_token)
        TOOL_OUTPUT_DIR.reset(dir_token)
    assert len(cut) <= 5_000
    assert cut.startswith(body[:100]) and cut.endswith(body[-100:])
    assert "45400 of 50000 characters omitted" in cut                 # 4,600 kept: the cap less the room for this note
    (saved,) = list(tmp_path.glob("Big-*.txt"))
    assert saved.read_text(encoding="utf-8") == body and str(saved) in cut


def test_a_result_within_the_cap_is_untouched_and_a_cut_one_is_not_cut_again(tmp_path: Path) -> None:
    tool = _Big("Big", "")
    token = TOOL_RESULT_CAP.set(MIN_RESULT_CHARS)
    dir_token = TOOL_OUTPUT_DIR.set(str(tmp_path))
    try:
        assert tool.truncate_result("short") == "short"
        once = tool.truncate_result(_body(20_000))
        assert len(once) <= MIN_RESULT_CHARS
        assert tool.truncate_result(once) == once
    finally:
        TOOL_OUTPUT_DIR.reset(dir_token)
        TOOL_RESULT_CAP.reset(token)


def test_without_a_cap_the_tokens_limit_applies_as_before() -> None:
    tool = _Big("Big", "")
    tool.max_result_tokens = 1_000
    assert tool.truncate_result("x" * 3_000) == "x" * 3_000
    assert "truncated: about" in tool.truncate_result("x" * 20_000) and "estimated tokens omitted" in tool.truncate_result("x" * 20_000)


# ---------------------------------------------------------------------------
# Through the executor
# ---------------------------------------------------------------------------


async def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caps: dict[str, int], name: str, body: str) -> str:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    registry = ToolRegistry()
    registry.register(_Big(name, body))
    settings = NerdvanaSettings()
    settings.tools.max_result_chars = caps
    executor = ToolExecutor(registry=registry, hooks=HookEngine(), settings=settings)
    context  = ToolContext(cwd=str(tmp_path))
    context.state["session_id"] = "caps"
    (result,) = await executor.run_batch([{"id": "1", "name": name, "input": {}}], context)
    return result.content


async def test_the_executor_applies_the_cap_of_the_tool_called(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = _body(40_000)
    assert await _run(tmp_path, monkeypatch, {}, "Big", body) == body                       # under the tool's own 30,000 tokens
    capped = await _run(tmp_path, monkeypatch, {"Big": 4_000}, "Big", body)
    assert len(capped) <= 4_000 and capped.startswith(body[:50]) and capped.endswith(body[-50:])
    assert list((tmp_path / "data" / "tool-output" / "caps").glob("Big-*.txt"))               # the whole output was saved
    assert await _run(tmp_path, monkeypatch, {"Other": 4_000}, "Big", body) == body        # another tool's cap does not apply


async def test_an_mcp_tool_is_capped_by_its_full_name_or_a_server_glob(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = _body(30_000)
    by_glob = await _run(tmp_path, monkeypatch, {"mcp__github__*": 3_000}, "mcp__github__search", body)
    by_name = await _run(tmp_path, monkeypatch, {"mcp__github__search": 2_000}, "mcp__github__search", body)
    other   = await _run(tmp_path, monkeypatch, {"mcp__github__*": 3_000}, "mcp__jira__search", body)
    assert len(by_glob) <= 3_000 and len(by_name) <= 2_000 and other == body


async def test_a_cap_above_the_tools_own_limit_lets_more_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NERDVANA_DATA_HOME", str(tmp_path / "data"))
    registry = ToolRegistry()
    tool     = _Big("Big", _body(40_000))
    tool.max_result_tokens = 1_000                          # the tool alone would cut this to about 4,000 characters
    registry.register(tool)
    settings = NerdvanaSettings()
    context  = ToolContext(cwd=str(tmp_path))
    executor = ToolExecutor(registry=registry, hooks=HookEngine(), settings=settings)
    (plain,) = await executor.run_batch([{"id": "1", "name": "Big", "input": {}}], context)
    settings.tools.max_result_chars = {"Big": 60_000}
    (wider,) = await executor.run_batch([{"id": "2", "name": "Big", "input": {}}], context)
    assert len(plain.content) < 5_000 and wider.content == _body(40_000)
