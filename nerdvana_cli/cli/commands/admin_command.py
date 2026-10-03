"""`nerdvana admin ...` — administrative sub-commands (Phase G1).

Currently hosts the ACL sub-group (``nerdvana admin acl ...``); future
admin-only operations should be wired into ``admin_app`` rather than added to
``main.py`` so the CLI surface stays organized.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

console     = Console()
err_console = Console(stderr=True)

_ACL_FILE_OPTION = typer.Option(
    None,
    "--acl-file",
    help = "Path to mcp_acl.yml (default: ~/.nerdvana/mcp_acl.yml).",
)

_RESTART_NOTICE = (
    "Change written to disk. Restart the MCP server to apply it; "
    "a running server keeps enforcing the ACL it loaded at startup."
)

admin_app = typer.Typer(
    name           = "admin",
    help           = "Administrative commands.",
    add_completion = False,
)

acl_app = typer.Typer(
    name           = "acl",
    help           = "Manage MCP access-control list (mcp_acl.yml).",
    add_completion = False,
)
admin_app.add_typer(acl_app)


@acl_app.command("list")
def acl_list(
    acl_file: Path | None = _ACL_FILE_OPTION,
) -> None:
    """List all clients and their roles."""
    from nerdvana_cli.server.acl import ACLManager

    mgr = ACLManager(acl_path=acl_file)
    mgr.load()

    console.print("[bold]Clients:[/bold]")
    for name, roles in sorted(mgr.list_clients().items()):
        console.print(f"  {name}: {', '.join(roles) or '(none)'}")

    console.print()
    console.print("[bold]Roles:[/bold]")
    for role, tools in sorted(mgr.list_roles().items()):
        console.print(f"  {role}: {', '.join(tools)}")


@acl_app.command("revoke")
def acl_revoke(
    key_prefix: str           = typer.Argument(..., help="Client name prefix to revoke"),
    acl_file:   Path | None   = _ACL_FILE_OPTION,
) -> None:
    """Revoke ACL entries for clients whose name starts with KEY_PREFIX."""
    from nerdvana_cli.server.acl import ACLManager, ACLPersistenceError

    mgr = ACLManager(acl_path=acl_file)
    mgr.load()

    try:
        removed = mgr.revoke(key_prefix)
    except ACLPersistenceError as exc:
        err_console.print(f"[red]Revoke failed, ACL unchanged:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if removed:
        for name in removed:
            console.print(f"Revoked: {name}")
        console.print(_RESTART_NOTICE)
    else:
        console.print(f"No clients found with prefix '{key_prefix}'.")


@acl_app.command("add")
def acl_add(
    client_name: str         = typer.Argument(..., help="Client name"),
    roles:       str         = typer.Argument(..., help="Comma-separated roles (e.g. 'read-only,edit')"),
    acl_file:    Path | None = _ACL_FILE_OPTION,
) -> None:
    """Add or update a client's role assignments."""
    from nerdvana_cli.server.acl import ACLManager, ACLPersistenceError

    role_list = [r.strip() for r in roles.split(",") if r.strip()]
    mgr       = ACLManager(acl_path=acl_file)
    mgr.load()

    try:
        mgr.add_client(client_name, role_list)
    except ACLPersistenceError as exc:
        err_console.print(f"[red]Update failed, ACL unchanged:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"Updated '{client_name}' -> {role_list}")
    console.print(_RESTART_NOTICE)
