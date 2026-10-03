"""Errors introduced by an edit are appended to the edit's result.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.hooks import HookEngine
from nerdvana_cli.core.tool import BaseTool, ToolCategory, ToolContext, ToolRegistry
from nerdvana_cli.core.tool_executor import ToolExecutor
from nerdvana_cli.types import ToolResult


class _Args:
    def __init__(self, path: str) -> None:
        self.path = path


class _FakeEdit(BaseTool[_Args]):
    name             = "FileEdit"
    description_text = "edit"
    input_schema     = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    args_class       = _Args
    category         = ToolCategory.WRITE

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="edited", is_error=self.fail)


class _Client:
    def __init__(self, rounds: list[list[dict[str, Any]]], delay: float = 0.0) -> None:
        self.rounds = rounds
        self.delay  = delay
        self.paths:  list[str] = []

    async def diagnostics(self, path: str) -> list[dict[str, Any]]:
        self.paths.append(path)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.rounds.pop(0) if self.rounds else []


class _Diag(BaseTool[Any]):
    name             = "lsp_diagnostics"
    description_text = "diagnostics"

    def __init__(self, client: _Client) -> None:
        self._client = client

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        return ToolResult(tool_use_id="", content="")


def _err(line: int, message: str) -> dict[str, Any]:
    return {"line": line, "col": 0, "severity": "error", "message": message}


async def _edit(tmp_path: Path, client: _Client | None, tool: _FakeEdit | None = None, **session: Any) -> ToolResult:
    registry = ToolRegistry()
    registry.register(tool or _FakeEdit())
    if client is not None:
        registry.register(_Diag(client))
    settings = NerdvanaSettings()
    for key, value in session.items():
        setattr(settings.session, key, value)
    executor = ToolExecutor(registry=registry, hooks=HookEngine(), settings=settings)
    results  = await executor.run_batch(
        [{"id": "c1", "name": "FileEdit", "input": {"path": "mod.py"}}],
        ToolContext(cwd=str(tmp_path)),
    )
    return results[0]


async def test_only_errors_the_edit_introduced_are_reported(tmp_path: Path) -> None:
    client = _Client([
        [_err(3, "old problem")],
        [_err(5, "old problem"), _err(9, "name 'x' is not defined"), {"line": 1, "severity": "warning", "message": "w"}],
    ])
    result = await _edit(tmp_path, client)
    assert "name 'x' is not defined" in result.content
    assert "line 9" in result.content
    assert "old problem" not in result.content
    assert client.paths == [str(tmp_path / "mod.py")] * 2


async def test_clean_edit_adds_nothing(tmp_path: Path) -> None:
    result = await _edit(tmp_path, _Client([[_err(1, "same")], [_err(2, "same")]]))
    assert result.content == "edited"


async def test_without_a_language_server_nothing_is_added(tmp_path: Path) -> None:
    assert (await _edit(tmp_path, None)).content == "edited"


async def test_failed_edit_is_not_followed_by_diagnostics(tmp_path: Path) -> None:
    client = _Client([[], [_err(1, "boom")]])
    result = await _edit(tmp_path, client, tool=_FakeEdit(fail=True))
    assert "boom" not in result.content
    assert len(client.paths) == 1


async def test_setting_turns_the_check_off(tmp_path: Path) -> None:
    client = _Client([[], [_err(1, "boom")]])
    result = await _edit(tmp_path, client, post_edit_diagnostics=False)
    assert result.content == "edited"
    assert client.paths == []


async def test_slow_server_is_skipped(tmp_path: Path, monkeypatch: Any) -> None:
    import nerdvana_cli.core.tool_executor as executor_module

    monkeypatch.setattr(executor_module, "_DIAGNOSTICS_TIMEOUT", 0.01)
    result = await _edit(tmp_path, _Client([[], [_err(1, "boom")]], delay=0.2))
    assert result.content == "edited"
