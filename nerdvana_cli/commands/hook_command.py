"""`nerdvana hook ...` — Claude Code / Codex / VSCode hook bridge (Phase G2).

Reads hook JSON from stdin and writes the response to stdout. Each subcommand
maps one-to-one onto a hook type defined in ``server.hook_schemas``.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

console = Console()

hook_app = typer.Typer(
    name           = "hook",
    help           = "Dispatch Claude Code / Codex / VSCode hook JSON via stdin/stdout.",
    add_completion = False,
)


def _resolve_db(db: str) -> Path | None:
    return Path(db) if db else None


@hook_app.command("pre-tool-use")
def hook_pre_tool_use(
    db: str = typer.Option("", "--db", help="Path to audit.sqlite (default: ~/.nerdvana/audit.sqlite)"),
) -> None:
    """Handle a pre-tool-use hook: read JSON from stdin, write response to stdout."""
    from nerdvana_cli.server.hook_bridge import run_hook
    raise typer.Exit(run_hook("pre-tool-use", db_path=_resolve_db(db)))


@hook_app.command("post-tool-use")
def hook_post_tool_use(
    db: str = typer.Option("", "--db", help="Path to audit.sqlite (default: ~/.nerdvana/audit.sqlite)"),
) -> None:
    """Handle a post-tool-use hook: read JSON from stdin, write response to stdout."""
    from nerdvana_cli.server.hook_bridge import run_hook
    raise typer.Exit(run_hook("post-tool-use", db_path=_resolve_db(db)))


@hook_app.command("prompt-submit")
def hook_prompt_submit(
    db: str = typer.Option("", "--db", help="Path to audit.sqlite (default: ~/.nerdvana/audit.sqlite)"),
) -> None:
    """Handle a prompt-submit hook: read JSON from stdin, write response to stdout."""
    from nerdvana_cli.server.hook_bridge import run_hook
    raise typer.Exit(run_hook("prompt-submit", db_path=_resolve_db(db)))


@hook_app.command("list")
def hook_list() -> None:
    """List supported hook types."""
    from nerdvana_cli.server.hook_schemas import HOOK_NAMES

    console.print("[bold]Supported hooks:[/bold]")
    for name in sorted(HOOK_NAMES):
        console.print(f"  {name}")
