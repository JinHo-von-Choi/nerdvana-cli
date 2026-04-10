"""MCP server configuration loader."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class McpServerConfig:
    """Configuration for a single MCP server."""

    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


def _expand_env(value: str) -> str:
    """Expand ${VAR} references in a string using os.environ.

    Undefined variables expand to empty string.
    """
    return re.sub(
        r"\$\{(\w+)\}",
        lambda m: os.environ.get(m.group(1), ""),
        value,
    )


def _expand_env_dict(d: dict[str, str]) -> dict[str, str]:
    return {k: _expand_env(v) for k, v in d.items()}


def _parse_server(name: str, raw: dict[str, Any]) -> McpServerConfig:
    transport = raw.get("type", "stdio")
    return McpServerConfig(
        name=name,
        transport=transport,
        command=raw.get("command", ""),
        args=raw.get("args", []),
        env=_expand_env_dict(raw.get("env", {})),
        url=_expand_env(raw.get("url", "")),
        headers=_expand_env_dict(raw.get("headers", {})),
    )


def load_mcp_config(
    cwd: str | None = None,
    global_path: str | None = None,
) -> dict[str, McpServerConfig]:
    """Load MCP server configs from project and global .mcp.json files.

    Args:
        cwd: Working directory to search for .mcp.json. Defaults to os.getcwd().
        global_path: Path to global config. Defaults to ~/.config/nerdvana-cli/mcp.json.

    Search order: global then project (cwd/.mcp.json).
    Project settings override global settings.
    """
    configs: dict[str, McpServerConfig] = {}

    gp = Path.home() / ".config" / "nerdvana-cli" / "mcp.json" if global_path is None else Path(global_path)
    project = Path.cwd() / ".mcp.json" if cwd is None else Path(cwd) / ".mcp.json"

    for path in (gp, project):
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                servers = data.get("mcpServers", {})
                for name, raw in servers.items():
                    configs[name] = _parse_server(name, raw)
            except (json.JSONDecodeError, OSError):
                pass

    return configs
