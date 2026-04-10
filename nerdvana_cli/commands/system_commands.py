"""System-level command handlers (help, update, init)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp


async def handle_help(app: NerdvanaApp, args: str) -> None:
    """Handle /help command — display available commands."""
    app._add_chat_message(
        "[bold]Commands[/bold]\n"
        "/help    -- Show help\n"
        "/quit    -- Exit\n"
        "/clear   -- Clear chat\n"
        "/init    -- Generate NIRNA.md\n"
        "/model   -- Show/change model\n"
        "/models     -- List available models\n"
        "/provider   -- Add/switch provider\n"
        "/mcp     -- MCP server status\n"
        "/skills  -- List available skills\n"
        "/tokens  -- Show usage\n"
        "/tools   -- List tools\n"
        "/update  -- Check and install updates\n"
        "Ctrl+C   -- Quit\n"
        "Ctrl+L   -- Clear chat"
    )


async def handle_update(app: NerdvanaApp, args: str) -> None:
    """Handle /update command — check for and install updates."""
    from nerdvana_cli.core.updater import run_self_update

    app._add_chat_message("[dim]Checking for updates...[/dim]")
    success, message = run_self_update()
    safe_msg = message.replace("[", "\\[")
    if success:
        app._add_chat_message(f"[green]{safe_msg}[/green]")
    else:
        app._add_chat_message(f"[red]{safe_msg}[/red]")


async def handle_init(app: NerdvanaApp, args: str) -> None:
    """Handle /init command — generate or update NIRNA.md."""
    nirna_path = os.path.join(app.settings.cwd, "NIRNA.md")
    exists = os.path.exists(nirna_path)
    app._add_chat_message(
        f"[dim]{'Analyzing existing' if exists else 'Generating'} NIRNA.md...[/dim]"
    )
    init_prompt = (
        f"Analyze this project directory ({app.settings.cwd}) and "
        f"{'suggest improvements to the existing' if exists else 'create a new'} NIRNA.md file.\n\n"
        "NIRNA.md is loaded into every NerdVana CLI session as project instructions. "
        "It must be concise — only include what the AI would get wrong without it.\n\n"
        "What to include:\n"
        "1. Build, test, lint commands (especially non-standard ones)\n"
        "2. High-level architecture (only what requires reading multiple files to understand)\n"
        "3. Code style rules that differ from language defaults\n"
        "4. Required env vars or setup steps\n"
        "5. Non-obvious gotchas\n\n"
        "What NOT to include:\n"
        "- Obvious instructions derivable from the codebase\n"
        "- Generic development practices\n"
        "- Every component or file structure listing\n"
        "- Sensitive information (API keys, tokens)\n\n"
        "Read the key project files first (manifest, README, config), then write the NIRNA.md content.\n"
        + ("Read the existing NIRNA.md first and suggest specific improvements.\n" if exists else "")
        + "Prefix the file with: # NIRNA.md\n"
        "Write the file using FileWrite tool."
    )
    app._generate_response(init_prompt)
