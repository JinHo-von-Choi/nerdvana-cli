"""Tool argument validation against input_schema.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from typing import Any

from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.hooks import HookEngine
from nerdvana_cli.core.schema_check import validate_arguments
from nerdvana_cli.core.tool import BaseTool, ToolContext, ToolRegistry
from nerdvana_cli.core.tool_executor import ToolExecutor
from nerdvana_cli.types import ToolResult

SCHEMA: dict[str, Any] = {
    "type":       "object",
    "properties": {
        "path":  {"type": "string"},
        "limit": {"type": "integer"},
        "mode":  {"type": "string", "enum": ["fast", "full"]},
        "note":  {"type": ["string", "null"]},
    },
    "required":   ["path"],
}


def test_valid_arguments_have_no_problems() -> None:
    assert validate_arguments(SCHEMA, {"path": "a.py", "limit": 3, "mode": "fast", "note": None}) == []


def test_missing_required_argument_is_reported() -> None:
    problems = validate_arguments(SCHEMA, {"limit": 3})
    assert any("missing required" in p and "path" in p for p in problems)


def test_unknown_argument_is_reported_with_expected_names() -> None:
    problems = validate_arguments(SCHEMA, {"path": "a.py", "file_path": "b.py"})
    assert len(problems) == 1
    assert "file_path" in problems[0]
    assert "limit" in problems[0]


def test_unknown_argument_allowed_when_not_rejecting_or_schema_allows() -> None:
    assert validate_arguments(SCHEMA, {"path": "a", "extra": 1}, reject_unknown=False) == []
    assert validate_arguments({**SCHEMA, "additionalProperties": True}, {"path": "a", "extra": 1}) == []


def test_bool_is_not_an_integer() -> None:
    problems = validate_arguments(SCHEMA, {"path": "a", "limit": True})
    assert any("limit" in p for p in problems)


def test_enum_violation_is_reported() -> None:
    assert any("mode" in p for p in validate_arguments(SCHEMA, {"path": "a", "mode": "slow"}))


def test_non_object_arguments_are_rejected() -> None:
    assert validate_arguments(SCHEMA, ["a"]) != []


class _Recorder(BaseTool[Any]):
    name             = "Rec"
    description_text = "records"
    input_schema     = SCHEMA

    def __init__(self) -> None:
        self.calls = 0

    async def call(self, args: Any, context: Any, can_use_tool: Any = None, on_progress: Any = None) -> ToolResult:
        self.calls += 1
        return ToolResult(tool_use_id="", content="ok")


async def test_executor_rejects_bad_arguments_without_calling_the_tool(tmp_path: Any) -> None:
    tool     = _Recorder()
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(registry=registry, hooks=HookEngine(), settings=NerdvanaSettings())

    results = await executor.run_batch(
        [{"id": "call_1", "name": "Rec", "input": {"path": "a", "pth": "typo"}}],
        ToolContext(cwd=str(tmp_path)),
    )

    assert results[0].is_error
    assert "pth" in results[0].content
    assert tool.calls == 0
