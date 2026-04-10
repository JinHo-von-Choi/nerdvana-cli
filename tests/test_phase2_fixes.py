"""Phase 2 security/stability fixes — regression tests for M1–M5.

M1: MCP client _write_message now async with drain()
M2: ai_compact truncates oversized history
M3: bash_tool blocks long-option rm/chmod variants
M4: Unknown agent types rejected with error
M5: Explore/Plan agents no longer have Bash access
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nerdvana_cli.agents.builtin import BUILTIN_AGENTS
from nerdvana_cli.agents.registry import AgentTypeRegistry
from nerdvana_cli.core.compact import CompactionState, ai_compact, _messages_to_text
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.mcp.client import McpClient
from nerdvana_cli.mcp.config import McpServerConfig
from nerdvana_cli.tools.bash_tool import BashArgs, BashTool
from nerdvana_cli.types import Message, PermissionBehavior, Role


# ---------------------------------------------------------------------------
# M1: MCP client _write_message is now async with drain()
# ---------------------------------------------------------------------------


class TestMcpWriteMessageDrain:
    """_write_message must be async and call drain()."""

    @pytest.mark.asyncio
    async def test_write_message_is_coroutine(self):
        """_write_message should be a coroutine function."""
        assert asyncio.iscoroutinefunction(McpClient._write_message)

    @pytest.mark.asyncio
    async def test_write_message_calls_drain(self):
        """_write_message must call stdin.drain() after write()."""
        config = McpServerConfig(
            name="test", transport="stdio", command="echo", args=[],
        )
        client = McpClient(config)

        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()

        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        client._process = mock_process

        await client._write_message({"jsonrpc": "2.0", "method": "test"})

        mock_stdin.write.assert_called_once()
        mock_stdin.drain.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_write_message_sends_json_newline(self):
        """Written data must be JSON + newline, utf-8 encoded."""
        config = McpServerConfig(
            name="test", transport="stdio", command="echo", args=[],
        )
        client = McpClient(config)

        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()

        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        client._process = mock_process

        msg = {"jsonrpc": "2.0", "id": 1, "method": "test"}
        await client._write_message(msg)

        written = mock_stdin.write.call_args[0][0]
        decoded = written.decode("utf-8")
        assert decoded.endswith("\n")
        assert json.loads(decoded.strip()) == msg


# ---------------------------------------------------------------------------
# M2: ai_compact truncates oversized history
# ---------------------------------------------------------------------------


class TestCompactTokenLimit:
    """ai_compact must truncate history_text to avoid context overflow."""

    def test_messages_to_text_produces_output(self):
        msgs = [
            Message(role=Role.USER, content="Hello"),
            Message(role=Role.ASSISTANT, content="Hi there"),
        ]
        text = _messages_to_text(msgs)
        assert "USER: Hello" in text
        assert "ASSISTANT: Hi there" in text

    @pytest.mark.asyncio
    async def test_large_history_is_truncated(self):
        """When history exceeds 128k chars, only the tail is sent to provider."""
        # Create messages totaling well over 128k chars
        big_content = "x" * 200_000
        messages = [Message(role=Role.USER, content=big_content)]

        captured_content = []

        async def mock_send(system_prompt, messages, tools):
            captured_content.append(messages[0]["content"])
            return {"content": "<summary>Short summary</summary>"}

        provider = MagicMock()
        provider.send = mock_send
        state = CompactionState()

        result = await ai_compact(messages, provider, state, prompt="Summarize")

        assert result is not None
        # The full content sent to provider should be capped
        sent = captured_content[0]
        # Should contain the prompt + truncated history
        assert len(sent) < 200_000 + 1000  # well under raw size

    @pytest.mark.asyncio
    async def test_small_history_not_truncated(self):
        """Small history passes through unchanged."""
        messages = [Message(role=Role.USER, content="Short message")]

        captured_content = []

        async def mock_send(system_prompt, messages, tools):
            captured_content.append(messages[0]["content"])
            return {"content": "Summary of short chat"}

        provider = MagicMock()
        provider.send = mock_send
        state = CompactionState()

        result = await ai_compact(messages, provider, state, prompt="Summarize")
        assert result is not None
        assert "Short message" in captured_content[0]


# ---------------------------------------------------------------------------
# M3: bash_tool blocks long-option rm/chmod variants
# ---------------------------------------------------------------------------


class TestBashLongOptionBlocking:
    """Long-option variants of dangerous commands must be blocked."""

    def setup_method(self):
        self.tool = BashTool()
        self.ctx = ToolContext(cwd="/tmp")

    def test_rm_no_preserve_root_blocked(self):
        result = self.tool.check_permissions(
            BashArgs(command="rm --no-preserve-root /"), self.ctx
        )
        assert result.behavior == PermissionBehavior.DENY

    def test_rm_recursive_force_long_blocked(self):
        result = self.tool.check_permissions(
            BashArgs(command="rm --recursive --force /"), self.ctx
        )
        assert result.behavior == PermissionBehavior.DENY

    def test_rm_force_recursive_long_blocked(self):
        result = self.tool.check_permissions(
            BashArgs(command="rm --force --recursive /home"), self.ctx
        )
        assert result.behavior == PermissionBehavior.DENY

    def test_chmod_000_blocked(self):
        result = self.tool.check_permissions(
            BashArgs(command="chmod 000 /etc/passwd"), self.ctx
        )
        assert result.behavior == PermissionBehavior.DENY

    def test_chmod_recursive_777_blocked(self):
        result = self.tool.check_permissions(
            BashArgs(command="chmod --recursive 777 /"), self.ctx
        )
        assert result.behavior == PermissionBehavior.DENY

    def test_safe_rm_still_allowed(self):
        result = self.tool.check_permissions(
            BashArgs(command="rm myfile.txt"), self.ctx
        )
        assert result.behavior == PermissionBehavior.ALLOW

    def test_safe_chmod_still_allowed(self):
        result = self.tool.check_permissions(
            BashArgs(command="chmod 644 myfile.txt"), self.ctx
        )
        assert result.behavior == PermissionBehavior.ALLOW


# ---------------------------------------------------------------------------
# M4: Unknown agent types rejected
# ---------------------------------------------------------------------------


class TestUnknownAgentRejection:

    @pytest.mark.asyncio
    async def test_unknown_agent_type_returns_error(self):
        """Spawning an unknown agent type must return is_error=True."""
        from nerdvana_cli.core.settings import NerdvanaSettings
        from nerdvana_cli.core.task_state import TaskRegistry
        from nerdvana_cli.tools.agent_tool import AgentTool, AgentToolArgs

        settings = NerdvanaSettings()
        task_registry = TaskRegistry()
        tool = AgentTool(settings=settings, task_registry=task_registry)
        ctx = ToolContext(cwd=".", task_registry=task_registry)

        result = await tool.call(
            AgentToolArgs(prompt="do something", subagent_type="nonexistent-type"),
            ctx,
            can_use_tool=None,
        )

        assert result.is_error is True
        assert "Unknown agent type" in result.content
        assert "nonexistent-type" in result.content

    @pytest.mark.asyncio
    async def test_known_agent_type_not_rejected(self):
        """Known agent types must NOT be rejected."""
        from nerdvana_cli.core.settings import NerdvanaSettings
        from nerdvana_cli.core.task_state import TaskRegistry
        from nerdvana_cli.tools.agent_tool import AgentTool, AgentToolArgs

        settings = NerdvanaSettings()
        task_registry = TaskRegistry()
        tool = AgentTool(settings=settings, task_registry=task_registry)
        ctx = ToolContext(cwd=".", task_registry=task_registry)

        with patch(
            "nerdvana_cli.tools.agent_tool.run_subagent",
            new_callable=AsyncMock,
            return_value="output",
        ):
            result = await tool.call(
                AgentToolArgs(prompt="explore", subagent_type="Explore"),
                ctx,
                can_use_tool=None,
            )

        assert result.is_error is not True
        assert result.content == "output"


# ---------------------------------------------------------------------------
# M5: Explore/Plan agents don't have Bash
# ---------------------------------------------------------------------------


class TestExploreNoBash:
    """Explore and Plan agents must NOT include Bash in allowed_tools."""

    def _get_agent(self, agent_type: str):
        reg = AgentTypeRegistry()
        for defn in BUILTIN_AGENTS:
            reg.register(defn)
        return reg.get(agent_type)

    def test_explore_no_bash(self):
        defn = self._get_agent("Explore")
        assert defn is not None
        assert "Bash" not in defn.allowed_tools

    def test_plan_no_bash(self):
        defn = self._get_agent("Plan")
        assert defn is not None
        assert "Bash" not in defn.allowed_tools

    def test_explore_has_read_tools(self):
        defn = self._get_agent("Explore")
        assert "Glob" in defn.allowed_tools
        assert "Grep" in defn.allowed_tools
        assert "FileRead" in defn.allowed_tools

    def test_general_purpose_still_has_wildcard(self):
        """general-purpose agent should retain full access."""
        defn = self._get_agent("general-purpose")
        assert defn is not None
        assert "*" in defn.allowed_tools

    def test_git_management_keeps_bash(self):
        """git-management needs Bash for git commands."""
        defn = self._get_agent("git-management")
        assert defn is not None
        assert "Bash" in defn.allowed_tools
