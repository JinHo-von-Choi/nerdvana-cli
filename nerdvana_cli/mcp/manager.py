"""Multi-server MCP lifecycle manager with failure isolation."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from nerdvana_cli.mcp.client import McpClient
from nerdvana_cli.mcp.config import McpServerConfig
from nerdvana_cli.mcp.tools import McpToolAdapter

logger = logging.getLogger(__name__)


class McpManager:
    """Manages multiple MCP server connections and their discovered tools.

    Provides parallel connection, tool discovery, and graceful shutdown.
    A single server failure is isolated and does not affect other servers.
    """

    def __init__(self, configs: dict[str, McpServerConfig]) -> None:
        self._configs = configs
        self._clients: dict[str, McpClient] = {}
        self._tools: list[McpToolAdapter] = []
        self._status: dict[str, bool] = {}

    async def _connect_one(
        self,
        name: str,
        config: McpServerConfig,
        timeout: float,
    ) -> tuple[str, str, list[McpToolAdapter]]:
        """Connect a single server and discover its tools.

        Returns (server_name, status_message, discovered_tools).
        Never raises; failures are captured in the status message.
        """
        client = McpClient(config)
        try:
            await asyncio.wait_for(client.connect(), timeout=timeout)
            raw_tools   = await client.list_tools()
            adapters    = [
                McpToolAdapter(name, tool_def, client)
                for tool_def in raw_tools
            ]
            self._clients[name] = client
            self._status[name]  = True
            msg = f"connected ({len(adapters)} tools)"
            logger.info("MCP %s: %s", name, msg)
            return name, msg, adapters

        except Exception as exc:
            self._status[name] = False
            msg = f"failed: {exc}"
            logger.warning("MCP %s: %s", name, msg)
            with contextlib.suppress(Exception):
                await client.disconnect()
            return name, msg, []

    async def connect_all(self, timeout: float = 30.0) -> dict[str, str]:
        """Connect to all configured servers in parallel and discover tools.

        Returns a dict mapping server name to a human-readable status string.
        One server's failure does not affect others (failure isolation).
        """
        if not self._configs:
            return {}

        tasks = [
            self._connect_one(name, config, timeout)
            for name, config in self._configs.items()
        ]
        results = await asyncio.gather(*tasks)

        status_report: dict[str, str] = {}
        for name, msg, adapters in results:
            status_report[name] = msg
            self._tools.extend(adapters)

        return status_report

    async def disconnect_all(self) -> None:
        """Disconnect all connected MCP clients gracefully."""
        disconnect_tasks = [
            client.disconnect()
            for client in self._clients.values()
        ]
        if disconnect_tasks:
            await asyncio.gather(*disconnect_tasks, return_exceptions=True)

        self._clients.clear()
        self._tools.clear()
        self._status.clear()
        logger.info("All MCP clients disconnected")

    def get_all_tools(self) -> list[McpToolAdapter]:
        """Return all discovered MCP tool adapters."""
        return list(self._tools)

    def get_status(self) -> dict[str, bool]:
        """Return connection status per server (True = connected)."""
        return dict(self._status)
