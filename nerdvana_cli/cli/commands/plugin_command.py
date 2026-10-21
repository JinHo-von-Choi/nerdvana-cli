"""``nerdvana plugin``: install, list and remove plugins (skills, commands, MCP servers and hooks in one package)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.markup import escape

from nerdvana_cli.cli.plugins import PluginError, install, installed, remove

console   = Console()
plugin_app = typer.Typer(name="plugin", help="Install skills, slash commands, MCP servers and hooks as one plugin.", no_args_is_help=True)


@plugin_app.command("install")
def plugin_install(
    source:      str  = typer.Argument(..., help="A plugin directory, or a git URL"),
    force:       bool = typer.Option(False, "--force", help="Replace an installed plugin of the same name and its skills"),
    allow_hooks: bool = typer.Option(False, "--allow-hooks", help="Also add the plugin's shell hooks (they run on every session)"),
) -> None:
    """Install a plugin from a directory or a git repository."""
    try:
        plugin = install(source, force=force, allow_hooks=allow_hooks)
    except PluginError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"Installed plugin [bold]{escape(plugin.name)}[/bold] {escape(plugin.version)}")
    for path in plugin.paths:
        console.print(f"  {escape(path)}")
    for server in plugin.mcp_servers:
        console.print(f"  MCP server {escape(server)}")
    for hook in plugin.hooks:
        note = hook.get("skipped") or f"hook {hook.get('event', '?')}: {hook.get('command', '')}"
        console.print(f"  [dim]{escape(str(note))}[/dim]")


@plugin_app.command("list")
def plugin_list() -> None:
    """List installed plugins."""
    plugins = installed()
    if not plugins:
        console.print("No plugins installed.")
        return
    for plugin in plugins:
        console.print(f"[cyan]{escape(plugin.name)}[/cyan] {escape(plugin.version)}  {escape(plugin.description)}  [dim]{escape(plugin.source)}[/dim]")


@plugin_app.command("remove")
def plugin_remove(name: str = typer.Argument(..., help="Name of the plugin")) -> None:
    """Remove what a plugin installed: its skills, commands, MCP servers and hooks."""
    try:
        plugin = remove(name)
    except PluginError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"Removed plugin {escape(plugin.name)}.")
