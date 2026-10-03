"""``nerdvana acp``: serve the agent to an editor over the Agent Client Protocol.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import asyncio
import sys

import typer

from nerdvana_cli.cli.runtime import APPROVAL_MODE_MAP, console_stderr, load_settings, run_migration_once

_INSTALL_HINT = 'The ACP agent needs the optional extra: pip install "nerdvana-cli[acp]"'


def acp_command(
    config:        str = typer.Option("", "--config", "-c", help="Config file path"),
    model:         str = typer.Option("", "--model", "-m", help="Model name"),
    provider:      str = typer.Option("", "--provider", "-p", help="AI provider"),
    approval_mode: str = typer.Option("", "--approval-mode", help="Preset mode: default | auto_edit | yolo | plan"),
    set_values:    list[str] | None = typer.Option(None, "--set", help="Override one setting for every session: section.field=value (repeatable)"),  # noqa: B008
) -> None:
    """Run as an Agent Client Protocol agent on stdin and stdout, for editors such as Zed.

    Standard output carries only protocol frames; diagnostics go to standard error.
    Each session works in the directory the editor gives it and starts from the same options.
    """
    try:
        import acp  # noqa: F401
    except ImportError:
        console_stderr.print(f"[red]{_INSTALL_HINT}[/red]")
        raise typer.Exit(2) from None

    from nerdvana_cli.acp.agent import NerdvanaAcpAgent
    from nerdvana_cli.acp.launch import LaunchOptions
    from nerdvana_cli.acp.server import serve

    mode = approval_mode.strip().lower()
    if mode and mode not in APPROVAL_MODE_MAP:
        console_stderr.print(f"[red]Error: --approval-mode must be one of {', '.join(APPROVAL_MODE_MAP)}.[/red]")
        raise typer.Exit(2)
    load_settings(config or None)
    run_migration_once(console_stderr)
    options = LaunchOptions(config_path=config or None, model=model, provider=provider, approval_mode=mode, set_values=list(set_values or []))
    try:
        asyncio.run(serve(NerdvanaAcpAgent(options)))
    except KeyboardInterrupt:
        sys.exit(130)
