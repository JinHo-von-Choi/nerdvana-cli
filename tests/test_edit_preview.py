"""Edit and write previews shown when a confirmation is asked.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.hooks.hooks import HookEngine
from nerdvana_cli.core.safety.policy import PermissionPolicy
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolRegistry
from nerdvana_cli.core.tool_executor import ToolExecutor
from nerdvana_cli.tools.file_tools import (
    FileEditArgs,
    FileEditTool,
    FileWriteArgs,
    FileWriteTool,
    _line_hash,
    unified_preview,
)
from nerdvana_cli.types import ToolResult

ORIGINAL = "alpha\nbeta\ngamma\n"


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=str(tmp_path))


def _file(tmp_path: Path, name: str = "a.txt", text: str = ORIGINAL) -> str:
    (tmp_path / name).write_text(text, encoding="utf-8")
    return name


# ---------------------------------------------------------------------------
# FileEdit
# ---------------------------------------------------------------------------


def test_edit_preview_shows_the_replaced_line(tmp_path: Path) -> None:
    name    = _file(tmp_path)
    preview = FileEditTool().preview_change(FileEditArgs(path=name, old_string="beta", new_string="BETA"), _ctx(tmp_path))
    assert preview is not None
    assert "--- a/a.txt" in preview
    assert "-beta" in preview
    assert "+BETA" in preview


def test_edit_preview_covers_replace_all(tmp_path: Path) -> None:
    name    = _file(tmp_path, text="x\nx\n")
    args    = FileEditArgs(path=name, old_string="x", new_string="y", replace_all=True)
    preview = FileEditTool().preview_change(args, _ctx(tmp_path))
    assert preview is not None
    assert preview.count("+y") == 2


def test_edit_preview_follows_an_anchor(tmp_path: Path) -> None:
    name    = _file(tmp_path)
    anchor  = f"2#{_line_hash('beta')}"
    preview = FileEditTool().preview_change(FileEditArgs(path=name, new_string="BETA\n", anchor_hash=anchor), _ctx(tmp_path))
    assert preview is not None
    assert "-beta" in preview
    assert "+BETA" in preview


def test_an_edit_the_tool_would_refuse_has_no_preview(tmp_path: Path) -> None:
    name = _file(tmp_path, text="x\nx\n")
    tool = FileEditTool()
    ctx  = _ctx(tmp_path)
    assert tool.preview_change(FileEditArgs(path=name, old_string="x", new_string="y"), ctx) is None  # ambiguous
    assert tool.preview_change(FileEditArgs(path=name, old_string="absent", new_string="y"), ctx) is None
    assert tool.preview_change(FileEditArgs(path=name, new_string="y", anchor_hash="not-an-anchor"), ctx) is None
    assert tool.preview_change(FileEditArgs(path="missing.txt", old_string="x", new_string="y"), ctx) is None
    assert tool.preview_change(FileEditArgs(path="../outside.txt", old_string="x", new_string="y"), ctx) is None


def test_previewing_changes_nothing_on_disk(tmp_path: Path) -> None:
    name = _file(tmp_path)
    FileEditTool().preview_change(FileEditArgs(path=name, old_string="beta", new_string="BETA"), _ctx(tmp_path))
    FileWriteTool().preview_change(FileWriteArgs(path=name, content="other\n"), _ctx(tmp_path))
    assert (tmp_path / name).read_text(encoding="utf-8") == ORIGINAL


# ---------------------------------------------------------------------------
# FileWrite
# ---------------------------------------------------------------------------


def test_write_preview_of_an_existing_file_is_a_diff(tmp_path: Path) -> None:
    name    = _file(tmp_path)
    preview = FileWriteTool().preview_change(FileWriteArgs(path=name, content="alpha\nBETA\ngamma\n"), _ctx(tmp_path))
    assert preview is not None
    assert "-beta" in preview
    assert "+BETA" in preview
    assert "-alpha" not in preview


def test_write_preview_of_a_new_file_lists_its_lines(tmp_path: Path) -> None:
    preview = FileWriteTool().preview_change(FileWriteArgs(path="new.txt", content="one\ntwo\n"), _ctx(tmp_path))
    assert preview is not None
    assert "--- /dev/null" in preview
    assert "+one" in preview
    assert "+two" in preview


def test_an_unchanged_write_has_no_preview(tmp_path: Path) -> None:
    name = _file(tmp_path)
    assert FileWriteTool().preview_change(FileWriteArgs(path=name, content=ORIGINAL), _ctx(tmp_path)) is None


def test_a_long_diff_is_cut_with_a_note() -> None:
    preview = unified_preview("big.txt", "", "".join(f"line {i}\n" for i in range(500)))
    assert preview is not None
    assert len(preview.splitlines()) <= 122
    assert "more diff line(s) not shown" in preview


# ---------------------------------------------------------------------------
# In the confirmation
# ---------------------------------------------------------------------------


def _executor(*tools: BaseTool[Any]) -> ToolExecutor:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return ToolExecutor(
        registry = registry,
        hooks    = HookEngine(),
        settings = NerdvanaSettings(),
        policy   = PermissionPolicy(trust_level="strict"),
    )


async def test_the_question_carries_the_diff_of_the_change(tmp_path: Path) -> None:
    name    = _file(tmp_path)
    confirm = AsyncMock(return_value=False)
    context = ToolContext(cwd=str(tmp_path), confirm=confirm)
    context.state["session_id"] = "preview"
    from nerdvana_cli.tools import read_ledger
    read_ledger.record(read_ledger.session_key(context), read_ledger.resolve_key(name, str(tmp_path)),
                       read_ledger.content_digest(ORIGINAL.encode()))

    results = await _executor(FileEditTool()).run_batch(
        [{"id": "c1", "name": "FileEdit", "input": {"path": name, "old_string": "beta", "new_string": "BETA"}}],
        context,
    )

    assert results[0].is_error
    message = confirm.await_args.args[1]
    assert "-beta" in message
    assert "+BETA" in message
    assert (tmp_path / name).read_text(encoding="utf-8") == ORIGINAL


class _Plain(BaseTool[Any]):
    name             = "Plain"
    description_text = "no preview"
    category         = ToolCategory.WRITE

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="ran")


class _Broken(_Plain):
    name = "Broken"

    def preview_change(self, args: Any, context: Any) -> str | None:
        raise RuntimeError("cannot preview")


async def test_a_tool_without_a_preview_is_asked_about_as_before(tmp_path: Path) -> None:
    confirm = AsyncMock(return_value=True)
    results = await _executor(_Plain()).run_batch(
        [{"id": "c1", "name": "Plain", "input": {}}], ToolContext(cwd=str(tmp_path), confirm=confirm),
    )
    assert not results[0].is_error
    assert "\n\n" not in confirm.await_args.args[1]


async def test_a_preview_that_fails_does_not_block_the_question(tmp_path: Path) -> None:
    confirm = AsyncMock(return_value=True)
    results = await _executor(_Broken()).run_batch(
        [{"id": "c1", "name": "Broken", "input": {}}], ToolContext(cwd=str(tmp_path), confirm=confirm),
    )
    assert not results[0].is_error
    confirm.assert_awaited_once()
