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


@hook_app.command("trust")
def hook_trust(
    path: str = typer.Argument(..., help="Path to the project hook file to approve"),
) -> None:
    """Approve a project-local hook so it may run.

    Approval binds to the file's current bytes. Editing the file afterwards
    revokes it until this is run again.
    """
    from nerdvana_cli.core.user_hooks import trust_project_hook

    target = Path(path)
    if not target.is_file():
        console.print(f"[red]Not a file:[/red] {target}")
        raise typer.Exit(code=1)

    try:
        digest = trust_project_hook(target)
    except OSError as exc:
        console.print(f"[red]Could not record approval:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"Approved {target.resolve()}")
    console.print(f"  digest {digest}")
    console.print("Project hooks also need [bold]hooks.allow_project_hooks[/bold] set to true.")


@hook_app.command("revoke")
def hook_revoke(
    path: str = typer.Argument(..., help="Path to the project hook file to revoke"),
) -> None:
    """Drop the approval recorded for a project-local hook."""
    from nerdvana_cli.core.user_hooks import revoke_project_hook

    target = Path(path)
    try:
        removed = revoke_project_hook(target)
    except OSError as exc:
        console.print(f"[red]Could not update the approval record:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if not removed:
        console.print(f"No approval recorded for {target.resolve()}")
        raise typer.Exit(code=1)
    console.print(f"Revoked {target.resolve()}")


@hook_app.command("trusted")
def hook_trusted() -> None:
    """List approved project hooks and flag any whose contents changed."""
    from nerdvana_cli.core.user_hooks import (
        hook_digest,
        load_trust_record,
        project_hook_trust_path,
    )

    record = load_trust_record()
    if not record:
        console.print(f"No approvals recorded in {project_hook_trust_path()}")
        return

    console.print(f"[bold]Approved project hooks[/bold] ({project_hook_trust_path()}):")
    for stored_path, approved_digest in sorted(record.items()):
        candidate = Path(stored_path)
        if not candidate.is_file():
            state = "[yellow]file is gone[/yellow]"
        elif hook_digest(candidate) != approved_digest:
            state = "[red]contents changed since approval[/red]"
        else:
            state = "[green]current[/green]"
        console.print(f"  {stored_path}  {state}")
