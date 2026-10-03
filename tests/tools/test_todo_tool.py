"""Tests for TodoWriteTool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.todo_tool import (
    TodoWriteArgs,
    TodoWriteTool,
    _sanitize_session_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(session_id: str | None = None, tmp_home: Path | None = None) -> ToolContext:
    ctx = ToolContext(cwd="/tmp")
    if session_id is not None:
        ctx.state["session_id"] = session_id
    return ctx


def _sample_todos() -> list[dict]:
    return [
        {"content": "Write tests",    "status": "completed",  "activeForm": "writing tests"},
        {"content": "Review PR",      "status": "in_progress", "activeForm": "reviewing PR"},
        {"content": "Update docs",    "status": "pending",     "activeForm": "updating docs"},
    ]


# ---------------------------------------------------------------------------
# Unit: _sanitize_session_id
# ---------------------------------------------------------------------------

class TestSanitizeSessionId:
    def test_normal_id_preserved(self):
        assert _sanitize_session_id("abc-123") == "abc-123"

    def test_dot_separated_allowed(self):
        assert _sanitize_session_id("session.v1.0") == "session.v1.0"

    def test_path_traversal_replaced(self):
        assert _sanitize_session_id("../../../etc") == "default"

    def test_slash_replaced(self):
        assert _sanitize_session_id("foo/bar") == "default"

    def test_empty_returns_default(self):
        assert _sanitize_session_id("") == "default"

    def test_null_bytes_replaced(self):
        assert _sanitize_session_id("abc\x00def") == "default"


# ---------------------------------------------------------------------------
# Integration: TodoWriteTool.call
# ---------------------------------------------------------------------------

class TestTodoWriteTool:
    @pytest.fixture(autouse=True)
    def _patch_todos_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Redirect _todos_dir() to a temp directory for each test."""
        fake_dir = tmp_path / ".nerdvana" / "todos"
        fake_dir.mkdir(parents=True)
        monkeypatch.setattr("nerdvana_cli.tools.todo_tool._todos_dir", lambda: fake_dir)
        self._todos_dir = fake_dir

    @pytest.fixture
    def tool(self) -> TodoWriteTool:
        return TodoWriteTool()

    # ------------------------------------------------------------------
    # Basic save + file content
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_basic_save(self, tool: TodoWriteTool):
        ctx  = _make_ctx("sess-01")
        args = TodoWriteArgs(todos=_sample_todos())
        result = await tool.call(args, ctx)

        assert not result.is_error
        payload = json.loads(result.content)
        assert len(payload["todos"]) == 3
        assert "saved_to" in payload

        saved_file = Path(payload["saved_to"])
        assert saved_file.exists()
        on_disk = json.loads(saved_file.read_text())
        assert on_disk["todos"] == _sample_todos()

    # ------------------------------------------------------------------
    # Empty list removes all todos
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_empty_list_clears(self, tool: TodoWriteTool):
        ctx = _make_ctx("sess-02")

        await tool.call(TodoWriteArgs(todos=_sample_todos()), ctx)
        result = await tool.call(TodoWriteArgs(todos=[]), ctx)

        assert not result.is_error
        payload = json.loads(result.content)
        assert payload["todos"] == []
        on_disk = json.loads(Path(payload["saved_to"]).read_text())
        assert on_disk["todos"] == []

    # ------------------------------------------------------------------
    # Idempotency: second call overwrites first
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_overwrite_on_second_call(self, tool: TodoWriteTool):
        ctx = _make_ctx("sess-03")

        first_todos = [{"content": "First task", "status": "pending", "activeForm": "doing first"}]
        await tool.call(TodoWriteArgs(todos=first_todos), ctx)

        second_todos = [{"content": "Second task", "status": "completed", "activeForm": "done"}]
        result = await tool.call(TodoWriteArgs(todos=second_todos), ctx)

        assert not result.is_error
        payload    = json.loads(result.content)
        on_disk    = json.loads(Path(payload["saved_to"]).read_text())
        assert on_disk["todos"] == second_todos
        assert len(on_disk["todos"]) == 1

    # ------------------------------------------------------------------
    # Status enum validation
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_invalid_status_returns_error(self, tool: TodoWriteTool):
        ctx  = _make_ctx("sess-04")
        bad  = [{"content": "X", "status": "done_maybe", "activeForm": "doing"}]
        result = await tool.call(TodoWriteArgs(todos=bad), ctx)

        assert result.is_error
        assert "done_maybe" in result.content

    @pytest.mark.asyncio
    async def test_missing_required_field_returns_error(self, tool: TodoWriteTool):
        ctx  = _make_ctx("sess-05")
        bad  = [{"content": "X", "status": "pending"}]   # activeForm missing
        result = await tool.call(TodoWriteArgs(todos=bad), ctx)

        assert result.is_error
        assert "activeForm" in result.content

    # ------------------------------------------------------------------
    # Session ID fallback
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_no_session_id_uses_default(self, tool: TodoWriteTool):
        ctx    = ToolContext(cwd="/tmp")   # no session_id in state
        todos  = [{"content": "Task", "status": "pending", "activeForm": "doing"}]
        result = await tool.call(TodoWriteArgs(todos=todos), ctx)

        assert not result.is_error
        payload = json.loads(result.content)
        assert payload["saved_to"].endswith("default.json")

    @pytest.mark.asyncio
    async def test_empty_session_id_uses_default(self, tool: TodoWriteTool):
        ctx   = _make_ctx("")
        todos = [{"content": "Task", "status": "pending", "activeForm": "doing"}]
        result = await tool.call(TodoWriteArgs(todos=todos), ctx)

        assert not result.is_error
        payload = json.loads(result.content)
        assert payload["saved_to"].endswith("default.json")

    # ------------------------------------------------------------------
    # Path traversal defence
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_path_traversal_in_session_id_stays_inside_todos_dir(
        self, tool: TodoWriteTool
    ):
        ctx    = _make_ctx("../../../evil")
        todos  = [{"content": "T", "status": "pending", "activeForm": "t"}]
        result = await tool.call(TodoWriteArgs(todos=todos), ctx)

        assert not result.is_error
        payload    = json.loads(result.content)
        saved_path = Path(payload["saved_to"])

        # The file must live inside the (mocked) todos directory.
        assert saved_path.parent.resolve() == self._todos_dir.resolve()
        # Sanitised to "default.json".
        assert saved_path.name == "default.json"

    # ------------------------------------------------------------------
    # Tool metadata
    # ------------------------------------------------------------------

    def test_tool_name(self, tool: TodoWriteTool):
        assert tool.name == "TodoWrite"

    def test_input_schema_has_todos(self, tool: TodoWriteTool):
        assert "todos" in tool.input_schema["properties"]
        assert "todos" in tool.input_schema["required"]
