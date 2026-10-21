"""MCP server configuration loader."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nerdvana_cli.core.config import paths
from nerdvana_cli.core.config.managed_policy import load_managed_policy

logger = logging.getLogger(__name__)


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
    # Confinement of a stdio server process (``off``, ``auto`` or ``require``); see mcp/sandbox.py.
    sandbox: str = "off"
    write_paths: list[str] = field(default_factory=list)
    network: bool = True


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


def _expand_paths(value: Any) -> list[str]:
    """The ``write_paths`` entries with ``${VAR}`` expanded; anything but a list of strings gives an empty list."""
    if not isinstance(value, list):
        return []
    return [_expand_env(path) for path in value if isinstance(path, str)]


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
        sandbox=raw.get("sandbox", "off"),
        write_paths=_expand_paths(raw.get("write_paths")),
        network=raw.get("network", True),
    )


def load_mcp_config(
    cwd: str | None = None,
    global_path: str | None = None,
    problems: list[str] | None = None,
) -> dict[str, McpServerConfig]:
    """Load MCP server configs from project and global .mcp.json files.

    Args:
        cwd: Working directory to search for .mcp.json. Defaults to os.getcwd().
        global_path: Path to global config. Defaults to ~/.nerdvana/mcp.json.
        problems: When given, receives one line per file or entry that was skipped.

    Search order: global then project (cwd/.mcp.json).
    Project settings override global settings. Servers outside the managed
    ``mcp.allowed_servers`` list are left out. A file that cannot be read and
    an entry that is not an object are skipped and logged, never dropped silently.
    """
    configs: dict[str, McpServerConfig] = {}
    skipped: list[str] = [] if problems is None else problems

    gp = paths.user_mcp_json() if global_path is None else Path(global_path)
    project = Path.cwd() / ".mcp.json" if cwd is None else Path(cwd) / ".mcp.json"

    for path in (gp, project):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            skipped.append(f"{path}: cannot be read ({exc})")
            continue
        servers = data.get("mcpServers", {}) if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            skipped.append(f"{path}: 'mcpServers' must be an object")
            continue
        for name, raw in servers.items():
            if not isinstance(raw, dict):
                skipped.append(f"{path}: server '{name}' must be an object")
                continue
            configs[name] = _parse_server(name, raw)

    for line in skipped:
        logger.warning("MCP config skipped: %s", line)
    return load_managed_policy().filter_mcp_servers(configs)
