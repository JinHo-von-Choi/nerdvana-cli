"""System-level command handlers (help, update, init)."""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp


async def handle_help(app: NerdvanaApp, args: str) -> None:
    """Handle /help command — display available commands.

    Command list is derived from SLASH_COMMANDS in ``nerdvana_cli.ui.app``
    (single source of truth).  Extra aliases and key bindings are appended.
    """
    from nerdvana_cli.ui.app import SLASH_COMMANDS

    lines = ["[bold]Commands[/bold]"]
    for cmd, desc in SLASH_COMMANDS:
        lines.append(f"{cmd:<20} {desc}")
    lines += [
        "",
        "/exit, /q              Aliases for /quit",
        "Ctrl+C                 Quit",
        "Ctrl+L                 Clear chat",
    ]
    app._add_chat_message("\n".join(lines))


async def _refresh_parism(app: NerdvanaApp) -> None:
    if shutil.which("npx") is None:
        app._add_chat_message("[red]npx not found. Install Node.js to refresh Parism.[/red]")
        return
    app._add_chat_message("[dim]Refreshing @nerdvana/parism to latest...[/dim]")
    proc = await asyncio.create_subprocess_exec(
        "npx",
        "-y",
        "--package=@nerdvana/parism@latest",
        "parism",
        "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    version = stdout.decode().strip() or "unknown"
    if proc.returncode == 0:
        app._add_chat_message(f"[green]Parism refreshed to {version}[/green]")
    else:
        err = stderr.decode().strip() or "unknown error"
        app._add_chat_message(f"[red]Parism refresh failed: {err}[/red]")


async def handle_update(app: NerdvanaApp, args: str) -> None:
    """Handle /update command. Sub-args:
    - empty: self-update via core.updater.run_self_update.
    - 'parism': force-refresh @nerdvana/parism through the npx cache.
    """
    target = args.strip().lower()
    if target == "parism":
        await _refresh_parism(app)
        return

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
