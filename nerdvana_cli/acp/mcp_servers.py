"""The MCP servers of an ACP session: the ones the editor passes in ``session/new``, added to those the project configures.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import logging
from typing import Any

from acp.schema import HttpMcpServer, McpServerStdio, SseMcpServer

from nerdvana_cli.mcp.config import McpServerConfig, load_mcp_config

logger = logging.getLogger(__name__)


def _to_config(server: Any) -> McpServerConfig | None:
    """The repository's form of one server the editor passed; None for a transport it cannot connect."""
    if isinstance(server, McpServerStdio):
        return McpServerConfig(
            name      = server.name,
            transport = "stdio",
            command   = server.command,
            args      = list(server.args),
            env       = {variable.name: variable.value for variable in server.env},
        )
    if isinstance(server, (HttpMcpServer, SseMcpServer)):
        return McpServerConfig(
            name      = server.name,
            transport = server.type,
            url       = server.url,
            headers   = {header.name: header.value for header in server.headers},
        )
    return None


def session_mcp_configs(cwd: str, servers: list[Any] | None) -> dict[str, McpServerConfig]:
    """The servers a session in *cwd* connects: global and project ones, then the editor's (which win on a name clash)."""
    configs = load_mcp_config(cwd=cwd)
    for server in servers or []:
        config = _to_config(server)
        if config is None:
            logger.warning("MCP server %s skipped: transport %s is not supported", getattr(server, "name", "?"), getattr(server, "type", "?"))
            continue
        configs[config.name] = config
    return configs
