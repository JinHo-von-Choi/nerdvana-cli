"""Multi-server MCP lifecycle manager with failure isolation."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from nerdvana_cli.mcp.client import McpClient
from nerdvana_cli.mcp.config import McpServerConfig
from nerdvana_cli.mcp.skills import SKILLS_EXTENSION, McpSkillFileTool, McpSkillLibrary
from nerdvana_cli.mcp.tools import McpToolAdapter, build_mcp_tool

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
        self._skills = McpSkillLibrary()
        self._skill_tool: McpSkillFileTool | None = None

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
                build_mcp_tool(name, tool_def, client)
                for tool_def in raw_tools
            ]
            self._clients[name] = client
            self._status[name]  = True
            msg = f"connected ({len(adapters)} tools{await self._discover_skills(name, client)})"
            logger.info("MCP %s: %s", name, msg)
            return name, msg, adapters

        except Exception as exc:
            self._status[name] = False
            msg = f"failed: {exc}"
            logger.warning("MCP %s: %s", name, msg)
            with contextlib.suppress(Exception):
                await client.disconnect()
            return name, msg, []

    async def _discover_skills(self, name: str, client: McpClient) -> str:
        """List the skills of a server that declares the skills extension; the text to append to its status."""
        if SKILLS_EXTENSION not in client.extensions:
            return ""
        try:
            count = await self._skills.discover(name, client)
        except Exception as exc:
            logger.warning("MCP %s: skills could not be listed: %s", name, exc)
            return ""
        return f", {count} skills" if count else ""

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
        self._skills = McpSkillLibrary()
        self._skill_tool = None
        self._status.clear()
        logger.info("All MCP clients disconnected")

    def get_all_tools(self) -> list[McpToolAdapter | McpSkillFileTool]:
        """Return all discovered MCP tool adapters, and the skill file reader when a server offers skills."""
        tools: list[McpToolAdapter | McpSkillFileTool] = list(self._tools)
        if self._skills.skills():
            self._skill_tool = self._skill_tool or McpSkillFileTool(self._skills)
            tools.append(self._skill_tool)
        return tools

    def get_confinement(self) -> dict[str, str]:
        """Return how each connected server process is confined (``confined``, ``unconfined`` or ``not applicable``)."""
        return {name: client.confinement for name, client in self._clients.items()}

    def get_status(self) -> dict[str, bool]:
        """Return connection status per server (True = connected)."""
        return dict(self._status)
