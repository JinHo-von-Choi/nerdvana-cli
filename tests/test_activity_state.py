"""Tests for ActivityState dataclass and summarize_tool_call helper."""

from __future__ import annotations

import pytest

from nerdvana_cli.core.activity_state import (
    ActivityState,
    summarize_tool_call,
)

# ---------------------------------------------------------------------------
# ActivityState defaults
# ---------------------------------------------------------------------------

class TestActivityStateDefaults:
    def test_default_phase(self) -> None:
        state = ActivityState()
        assert state.phase == "idle"

    def test_default_label(self) -> None:
        state = ActivityState()
        assert state.label == "Ready"

    def test_default_detail_and_tool(self) -> None:
        state = ActivityState()
        assert state.detail == ""
        assert state.tool_name == ""
        assert state.started_at is None

    def test_custom_fields(self) -> None:
        state = ActivityState(phase="thinking", label="Thinking", detail="...", tool_name="Bash", started_at=1.0)
        assert state.phase      == "thinking"
        assert state.label      == "Thinking"
        assert state.tool_name  == "Bash"
        assert state.started_at == 1.0


# ---------------------------------------------------------------------------
# Bash
# ---------------------------------------------------------------------------

class TestBash:
    def test_short_command(self) -> None:
        label, detail = summarize_tool_call("Bash", {"command": "ls -la"})
        assert label  == "Bash"
        assert detail == "ls -la"

    def test_exact_60_chars(self) -> None:
        cmd = "x" * 60
        label, detail = summarize_tool_call("Bash", {"command": cmd})
        assert label  == "Bash"
        assert detail == cmd
        assert "..." not in detail

    def test_over_60_truncated(self) -> None:
        cmd = "a" * 61
        label, detail = summarize_tool_call("Bash", {"command": cmd})
        assert label  == "Bash"
        assert len(detail) == 63          # 60 chars + "..."
        assert detail.endswith("...")

    def test_empty_command(self) -> None:
        label, detail = summarize_tool_call("Bash", {})
        assert label  == "Bash"
        assert detail == ""


# ---------------------------------------------------------------------------
# FileRead / FileWrite / FileEdit
# ---------------------------------------------------------------------------

class TestFileTools:
    @pytest.mark.parametrize("tool", ["FileRead", "FileWrite", "FileEdit"])
    def test_absolute_path_shortened(self, tool: str) -> None:
        label, detail = summarize_tool_call(tool, {"file_path": "/home/nirna/job/nerdvana-cli/nerdvana_cli/core/agent_loop.py"})
        assert label  == tool
        assert detail == "core/agent_loop.py"

    @pytest.mark.parametrize("tool", ["FileRead", "FileWrite", "FileEdit"])
    def test_two_level_path(self, tool: str) -> None:
        label, detail = summarize_tool_call(tool, {"file_path": "/some/dir/file.py"})
        assert label  == tool
        assert detail == "dir/file.py"

    @pytest.mark.parametrize("tool", ["FileRead", "FileWrite", "FileEdit"])
    def test_empty_path(self, tool: str) -> None:
        label, detail = summarize_tool_call(tool, {})
        assert label  == tool
        assert detail == ""


# ---------------------------------------------------------------------------
# Glob
# ---------------------------------------------------------------------------

class TestGlob:
    def test_short_pattern(self) -> None:
        label, detail = summarize_tool_call("Glob", {"pattern": "**/*.py"})
        assert label  == "Glob"
        assert detail == "**/*.py"

    def test_pattern_truncated_at_60(self) -> None:
        pat = "src/" + "a" * 60
        label, detail = summarize_tool_call("Glob", {"pattern": pat})
        assert label == "Glob"
        assert detail.endswith("...")
        assert len(detail) == 63


# ---------------------------------------------------------------------------
# Grep
# ---------------------------------------------------------------------------

class TestGrep:
    def test_pattern_and_path(self) -> None:
        label, detail = summarize_tool_call("Grep", {"pattern": "def foo", "path": "/project/src"})
        assert label  == "Grep"
        assert "def foo" in detail
        assert "src" in detail
        assert " in " in detail

    def test_pattern_only(self) -> None:
        label, detail = summarize_tool_call("Grep", {"pattern": "import re"})
        assert label  == "Grep"
        assert "import re" in detail

    def test_pattern_truncated_at_40(self) -> None:
        pat = "x" * 45
        label, detail = summarize_tool_call("Grep", {"pattern": pat})
        assert label == "Grep"
        # pattern portion ends with "..."
        assert "..." in detail

    def test_path_truncated_at_30(self) -> None:
        path = "/very/long/path/" + "d" * 30
        label, detail = summarize_tool_call("Grep", {"pattern": "foo", "path": path})
        assert label == "Grep"
        # path portion is included but truncated
        assert "foo" in detail


# ---------------------------------------------------------------------------
# Parism
# ---------------------------------------------------------------------------

class TestParism:
    def test_cmd_with_args(self) -> None:
        label, detail = summarize_tool_call("Parism", {"cmd": "run", "args": ["--flag", "value"]})
        assert label  == "Parism"
        assert detail.startswith("run")
        assert "--flag" in detail

    def test_cmd_only(self) -> None:
        label, detail = summarize_tool_call("Parism", {"cmd": "status"})
        assert label  == "Parism"
        assert detail == "status"

    def test_args_truncated(self) -> None:
        args = ["--very-long-argument"] * 5
        label, detail = summarize_tool_call("Parism", {"cmd": "exec", "args": args})
        assert label == "Parism"
        assert detail.startswith("exec")
        assert "..." in detail


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class TestAgent:
    def test_subagent_type(self) -> None:
        label, detail = summarize_tool_call("Agent", {"subagent_type": "researcher"})
        assert label  == "Agent"
        assert detail == "spawn: researcher"

    def test_empty_type(self) -> None:
        label, detail = summarize_tool_call("Agent", {})
        assert label  == "Agent"
        assert detail == "spawn: "


# ---------------------------------------------------------------------------
# Swarm
# ---------------------------------------------------------------------------

class TestSwarm:
    def test_agents_list_count(self) -> None:
        agents = [{"prompt": "task1"}, {"prompt": "task2"}, {"prompt": "task3"}]
        label, detail = summarize_tool_call("Swarm", {"agents": agents})
        assert label  == "Swarm"
        assert detail == "spawn 3 agents"

    def test_tasks_list_fallback(self) -> None:
        tasks = [{"prompt": "t1"}, {"prompt": "t2"}]
        label, detail = summarize_tool_call("Swarm", {"tasks": tasks})
        assert label  == "Swarm"
        assert detail == "spawn 2 agents"

    def test_empty_list(self) -> None:
        label, detail = summarize_tool_call("Swarm", {"agents": []})
        assert label  == "Swarm"
        assert detail == "spawn 0 agents"

    def test_missing_agents_key(self) -> None:
        label, detail = summarize_tool_call("Swarm", {})
        assert label  == "Swarm"
        assert detail == "spawn 0 agents"


# ---------------------------------------------------------------------------
# TeamCreate
# ---------------------------------------------------------------------------

class TestTeamCreate:
    def test_team_name(self) -> None:
        label, detail = summarize_tool_call("TeamCreate", {"team_name": "alpha"})
        assert label  == "TeamCreate"
        assert detail == "alpha"

    def test_empty(self) -> None:
        label, detail = summarize_tool_call("TeamCreate", {})
        assert label  == "TeamCreate"
        assert detail == ""


# ---------------------------------------------------------------------------
# SendMessage
# ---------------------------------------------------------------------------

class TestSendMessage:
    def test_to_field(self) -> None:
        label, detail = summarize_tool_call("SendMessage", {"to": "agent-7", "message": "hello"})
        assert label  == "SendMessage"
        assert detail == "to: agent-7"

    def test_empty(self) -> None:
        label, detail = summarize_tool_call("SendMessage", {})
        assert label  == "SendMessage"
        assert detail == "to: "


# ---------------------------------------------------------------------------
# TaskGet / TaskStop
# ---------------------------------------------------------------------------

class TestTaskGetStop:
    def test_task_get(self) -> None:
        label, detail = summarize_tool_call("TaskGet", {"task_id": "task-abc-123"})
        assert label  == "TaskGet"
        assert detail == "task-abc-123"

    def test_task_stop(self) -> None:
        label, detail = summarize_tool_call("TaskStop", {"task_id": "task-xyz"})
        assert label  == "TaskStop"
        assert detail == "task-xyz"

    def test_task_get_empty(self) -> None:
        label, detail = summarize_tool_call("TaskGet", {})
        assert label  == "TaskGet"
        assert detail == ""

    def test_task_stop_empty(self) -> None:
        label, detail = summarize_tool_call("TaskStop", {})
        assert label  == "TaskStop"
        assert detail == ""


# ---------------------------------------------------------------------------
# WebFetch
# ---------------------------------------------------------------------------

class TestWebFetch:
    def test_hostname_extracted(self) -> None:
        label, detail = summarize_tool_call("WebFetch", {"url": "https://api.example.com/v1/data"})
        assert label  == "WebFetch"
        assert detail == "api.example.com"

    def test_url_with_port(self) -> None:
        label, detail = summarize_tool_call("WebFetch", {"url": "http://localhost:8080/path"})
        assert label  == "WebFetch"
        assert detail == "localhost"

    def test_empty_url(self) -> None:
        label, detail = summarize_tool_call("WebFetch", {})
        assert label  == "WebFetch"
        # empty url -> urlparse gives no hostname; falls back to url string
        assert label == "WebFetch"


# ---------------------------------------------------------------------------
# WebSearch
# ---------------------------------------------------------------------------

class TestWebSearch:
    def test_short_query(self) -> None:
        label, detail = summarize_tool_call("WebSearch", {"query": "python type hints"})
        assert label  == "WebSearch"
        assert detail == "python type hints"

    def test_query_truncated_at_40(self) -> None:
        query = "q" * 45
        label, detail = summarize_tool_call("WebSearch", {"query": query})
        assert label  == "WebSearch"
        assert len(detail) == 43
        assert detail.endswith("...")

    def test_empty_query(self) -> None:
        label, detail = summarize_tool_call("WebSearch", {})
        assert label  == "WebSearch"
        assert detail == ""


# ---------------------------------------------------------------------------
# TodoWrite
# ---------------------------------------------------------------------------

class TestTodoWrite:
    def test_count(self) -> None:
        todos = [{"id": "1"}, {"id": "2"}, {"id": "3"}, {"id": "4"}]
        label, detail = summarize_tool_call("TodoWrite", {"todos": todos})
        assert label  == "TodoWrite"
        assert detail == "4 todos"

    def test_empty_list(self) -> None:
        label, detail = summarize_tool_call("TodoWrite", {"todos": []})
        assert label  == "TodoWrite"
        assert detail == "0 todos"

    def test_missing_key(self) -> None:
        label, detail = summarize_tool_call("TodoWrite", {})
        assert label  == "TodoWrite"
        assert detail == "0 todos"


# ---------------------------------------------------------------------------
# LSP tools
# ---------------------------------------------------------------------------

class TestLspTools:
    @pytest.mark.parametrize("tool", [
        "lsp_diagnostics",
        "lsp_goto_definition",
        "lsp_find_references",
        "lsp_rename",
    ])
    def test_file_path_only(self, tool: str) -> None:
        label, detail = summarize_tool_call(tool, {"file_path": "/project/src/foo.py"})
        assert label  == tool
        assert detail == "src/foo.py"

    @pytest.mark.parametrize("tool", [
        "lsp_goto_definition",
        "lsp_find_references",
        "lsp_rename",
    ])
    def test_file_and_symbol(self, tool: str) -> None:
        label, detail = summarize_tool_call(tool, {"file_path": "/project/src/foo.py", "symbol": "my_func"})
        assert label  == tool
        assert "src/foo.py" in detail
        assert "my_func" in detail
        assert ":" in detail

    def test_lsp_diagnostics_no_symbol(self) -> None:
        label, detail = summarize_tool_call("lsp_diagnostics", {"file_path": "/a/b/c.py"})
        assert label  == "lsp_diagnostics"
        assert detail == "b/c.py"

    def test_empty_input(self) -> None:
        label, detail = summarize_tool_call("lsp_goto_definition", {})
        assert label  == "lsp_goto_definition"
        assert detail == ""


# ---------------------------------------------------------------------------
# Symbol tools
# ---------------------------------------------------------------------------

class TestSymbolTools:
    @pytest.mark.parametrize("tool", [
        "get_symbols_overview",
        "find_symbol",
        "find_referencing_symbols",
        "replace_symbol_body",
        "insert_before_symbol",
        "insert_after_symbol",
        "safe_delete_symbol",
        "symbol_overview",
    ])
    def test_file_and_symbol(self, tool: str) -> None:
        label, detail = summarize_tool_call(tool, {
            "relative_path": "nerdvana_cli/core/agent_loop.py",
            "name_path": "AgentLoop/run",
        })
        assert label  == tool
        assert "agent_loop.py" in detail
        assert "AgentLoop/run" in detail

    @pytest.mark.parametrize("tool", [
        "get_symbols_overview",
        "symbol_overview",
    ])
    def test_file_only(self, tool: str) -> None:
        label, detail = summarize_tool_call(tool, {"relative_path": "nerdvana_cli/core/tool.py"})
        assert label  == tool
        assert "tool.py" in detail

    def test_symbol_only(self) -> None:
        label, detail = summarize_tool_call("find_symbol", {"name_path": "MyClass/method"})
        assert label  == "find_symbol"
        assert detail == "MyClass/method"

    def test_empty_input(self) -> None:
        label, detail = summarize_tool_call("find_symbol", {})
        assert label  == "find_symbol"
        assert detail == ""


# ---------------------------------------------------------------------------
# External project tools
# ---------------------------------------------------------------------------

class TestExternalProjectTools:
    def test_list_queryable_projects_no_args(self) -> None:
        label, detail = summarize_tool_call("ListQueryableProjects", {})
        assert label  == "ListQueryableProjects"
        assert detail == ""

    def test_register_external_project_name(self) -> None:
        label, detail = summarize_tool_call("RegisterExternalProject", {"name": "my-project", "path": "/some/path"})
        assert label  == "RegisterExternalProject"
        assert detail == "my-project"

    def test_query_external_project_name(self) -> None:
        label, detail = summarize_tool_call("QueryExternalProject", {"name": "my-project", "question": "what does foo do?"})
        assert label  == "QueryExternalProject"
        assert detail == "my-project"


# ---------------------------------------------------------------------------
# Unknown / fallback
# ---------------------------------------------------------------------------

class TestFallback:
    def test_unknown_tool_name(self) -> None:
        label, detail = summarize_tool_call("UnknownTool", {"some_key": "some_value"})
        assert label  == "UnknownTool"
        assert detail == ""

    def test_unknown_tool_empty_input(self) -> None:
        label, detail = summarize_tool_call("UnknownTool", {})
        assert label  == "UnknownTool"
        assert detail == ""

    def test_completely_novel_tool(self) -> None:
        label, detail = summarize_tool_call("FutureToolXYZ", {"x": 1, "y": 2})
        assert label  == "FutureToolXYZ"
        assert detail == ""

    def test_empty_input_dict_bash(self) -> None:
        """Ensure no KeyError on empty dict for any known tool."""
        for tool in (
            "Bash", "FileRead", "FileWrite", "FileEdit",
            "Glob", "Grep", "Parism", "Agent", "Swarm",
            "TeamCreate", "SendMessage", "TaskGet", "TaskStop",
            "WebFetch", "WebSearch", "TodoWrite",
        ):
            label, detail = summarize_tool_call(tool, {})
            assert label == tool
            assert isinstance(detail, str)
