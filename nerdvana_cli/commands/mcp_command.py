"""mcp sub-app — MCP server management.

Commands:
  mcp list    — list configured MCP servers
  mcp add     — add a new MCP server to ~/.nerdvana/mcp.json
  mcp remove  — remove an MCP server from ~/.nerdvana/mcp.json

The global config is ~/.nerdvana/mcp.json (paths.user_mcp_json()).
The file uses the same mcpServers schema as .mcp.json.

Author: 최진호
Date:   2026-04-29
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from nerdvana_cli.core import paths as core_paths

console = Console()

mcp_app = typer.Typer(
    name           = "mcp",
    help           = "MCP server management.",
    add_completion = False,
)


# ---------------------------------------------------------------------------
# Helpers — shared storage backend (same file that mcp.config.load_mcp_config reads)
# ---------------------------------------------------------------------------

def _config_path() -> Path:
    return core_paths.user_mcp_json()


def _load_raw() -> dict[str, Any]:
    """Read ~/.nerdvana/mcp.json, returning empty dict if absent or invalid."""
    path = _config_path()
    if not path.is_file():
        return {"mcpServers": {}}
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if "mcpServers" not in raw or not isinstance(raw["mcpServers"], dict):
            raw["mcpServers"] = {}
        return raw
    except (json.JSONDecodeError, OSError):
        return {"mcpServers": {}}


def _save_raw(data: dict[str, Any]) -> None:
    """Write data back to ~/.nerdvana/mcp.json, creating parent dirs as needed."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@mcp_app.command("list")
def mcp_list(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List configured MCP servers from the global config."""
    data    = _load_raw()
    servers = data.get("mcpServers", {})

    if json_output:
        console.print(json.dumps(servers, indent=2))
        return

    if not servers:
        console.print("[dim]No MCP servers configured.[/dim]")
        return

    console.print(f"[bold]MCP servers ({len(servers)})[/bold]")
    for name, cfg in servers.items():
        transport = cfg.get("type", "stdio")
        target    = cfg.get("url", "") or cfg.get("command", "")
        console.print(f"  [cyan]{name}[/cyan]  [{transport}]  {target}")


@mcp_app.command("add")
def mcp_add(
    name:      str = typer.Argument(..., help="Server name."),
    url:       str = typer.Option(..., "--url",       help="Server URL or stdio command."),
    transport: str = typer.Option("http", "--transport", help="Transport type: http | stdio"),
) -> None:
    """Add a new MCP server to the global config.

    If a server with the same name already exists, the command exits with an
    error. Use 'mcp remove' first to replace it.
    """
    if transport not in ("http", "stdio"):
        console.print(f"[red]Invalid transport '{transport}'. Use 'http' or 'stdio'.[/red]")
        raise typer.Exit(1)

    data    = _load_raw()
    servers = data["mcpServers"]

    if name in servers:
        console.print(
            f"[yellow]Server '{name}' already exists. "
            "Run 'mcp remove {name}' first.[/yellow]"
        )
        raise typer.Exit(1)

    if transport == "http":
        servers[name] = {"type": "http", "url": url}
    else:
        servers[name] = {"type": "stdio", "command": url}

    _save_raw(data)
    console.print(f"Added MCP server '[cyan]{name}[/cyan]' ({transport}: {url}).")


@mcp_app.command("remove")
def mcp_remove(
    name: str = typer.Argument(..., help="Server name to remove."),
) -> None:
    """Remove an MCP server from the global config."""
    data    = _load_raw()
    servers = data["mcpServers"]

    if name not in servers:
        console.print(f"[red]Server '{name}' not found.[/red]")
        raise typer.Exit(1)

    del servers[name]
    _save_raw(data)
    console.print(f"Removed MCP server '[cyan]{name}[/cyan]'.")
