"""Tests for McpManager — multi-server lifecycle with failure isolation."""

from unittest.mock import AsyncMock, patch

import pytest

from nerdvana_cli.mcp.config import McpServerConfig
from nerdvana_cli.mcp.manager import McpManager


def _make_config(name: str) -> McpServerConfig:
    return McpServerConfig(
        name=name,
        transport="stdio",
        command="node",
        args=[f"/path/to/{name}.js"],
    )


def _make_tool_def(tool_name: str) -> dict:
    return {
        "name": tool_name,
        "description": f"Tool {tool_name}",
        "inputSchema": {"type": "object", "properties": {}},
    }


class TestMcpManagerConnectAll:
    """connect_all: parallel connection + tool discovery."""

    @pytest.mark.asyncio
    async def test_successful_connect_and_tool_discovery(self):
        configs = {
            "alpha": _make_config("alpha"),
            "beta": _make_config("beta"),
        }
        manager = McpManager(configs)

        with patch("nerdvana_cli.mcp.manager.McpClient") as MockClient:  # noqa: N806
            instance_alpha        = AsyncMock()
            instance_alpha.connect = AsyncMock()
            instance_alpha.list_tools = AsyncMock(return_value=[
                _make_tool_def("search"),
                _make_tool_def("query"),
            ])

            instance_beta        = AsyncMock()
            instance_beta.connect = AsyncMock()
            instance_beta.list_tools = AsyncMock(return_value=[
                _make_tool_def("run"),
            ])

            call_count = 0
            def side_effect(config):
                nonlocal call_count
                call_count += 1
                if config.name == "alpha":
                    return instance_alpha
                return instance_beta

            MockClient.side_effect = side_effect

            status = await manager.connect_all(timeout=10.0)

        assert "alpha" in status
        assert "beta" in status
        assert "connected" in status["alpha"]
        assert "2 tools" in status["alpha"]
        assert "connected" in status["beta"]
        assert "1 tools" in status["beta"]

        tools = manager.get_all_tools()
        assert len(tools) == 3
        tool_names = {t.name for t in tools}
        assert "mcp__alpha__search" in tool_names
        assert "mcp__alpha__query" in tool_names
        assert "mcp__beta__run" in tool_names

        conn_status = manager.get_status()
        assert conn_status["alpha"] is True
        assert conn_status["beta"] is True


class TestMcpManagerFailureIsolation:
    """One server failure must not affect others."""

    @pytest.mark.asyncio
    async def test_failed_connection_recorded_without_affecting_others(self):
        configs = {
            "good": _make_config("good"),
            "bad": _make_config("bad"),
        }
        manager = McpManager(configs)

        with patch("nerdvana_cli.mcp.manager.McpClient") as MockClient:  # noqa: N806
            instance_good        = AsyncMock()
            instance_good.connect = AsyncMock()
            instance_good.list_tools = AsyncMock(return_value=[
                _make_tool_def("tool_a"),
            ])

            instance_bad        = AsyncMock()
            instance_bad.connect = AsyncMock(
                side_effect=ConnectionError("server unreachable")
            )
            instance_bad.disconnect = AsyncMock()

            def side_effect(config):
                if config.name == "good":
                    return instance_good
                return instance_bad

            MockClient.side_effect = side_effect

            status = await manager.connect_all(timeout=5.0)

        assert "connected" in status["good"]
        assert "failed" in status["bad"]

        tools = manager.get_all_tools()
        assert len(tools) == 1
        assert tools[0].name == "mcp__good__tool_a"

        conn_status = manager.get_status()
        assert conn_status["good"] is True
        assert conn_status["bad"] is False


class TestMcpManagerDisconnect:
    """disconnect_all: clean shutdown of all clients."""

    @pytest.mark.asyncio
    async def test_disconnect_all_clears_state(self):
        configs = {
            "srv": _make_config("srv"),
        }
        manager = McpManager(configs)

        with patch("nerdvana_cli.mcp.manager.McpClient") as MockClient:  # noqa: N806
            instance          = AsyncMock()
            instance.connect  = AsyncMock()
            instance.list_tools = AsyncMock(return_value=[
                _make_tool_def("ping"),
            ])
            instance.disconnect = AsyncMock()
            MockClient.return_value = instance

            await manager.connect_all(timeout=5.0)

        assert len(manager.get_all_tools()) == 1
        assert manager.get_status()["srv"] is True

        await manager.disconnect_all()

        assert len(manager.get_all_tools()) == 0
        assert manager.get_status() == {}
        instance.disconnect.assert_awaited_once()
