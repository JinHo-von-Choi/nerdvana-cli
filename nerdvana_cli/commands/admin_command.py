"""`nerdvana admin ...` — administrative sub-commands (Phase G1).

Currently hosts the ACL sub-group (``nerdvana admin acl ...``); future
admin-only operations should be wired into ``admin_app`` rather than added to
``main.py`` so the CLI surface stays organized.
"""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()

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
def acl_list() -> None:
    """List all clients and their roles."""
    from nerdvana_cli.server.acl import ACLManager

    mgr = ACLManager()
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
    key_prefix: str = typer.Argument(..., help="Client name prefix to revoke"),
) -> None:
    """Revoke ACL entries for clients whose name starts with KEY_PREFIX."""
    from nerdvana_cli.server.acl import ACLManager

    mgr     = ACLManager()
    mgr.load()
    removed = mgr.revoke(key_prefix)

    if removed:
        for name in removed:
            console.print(f"Revoked: {name}")
    else:
        console.print(f"No clients found with prefix '{key_prefix}'.")


@acl_app.command("add")
def acl_add(
    client_name: str = typer.Argument(..., help="Client name"),
    roles:       str = typer.Argument(..., help="Comma-separated roles (e.g. 'read-only,edit')"),
) -> None:
    """Add or update a client's role assignments."""
    from nerdvana_cli.server.acl import ACLManager

    role_list = [r.strip() for r in roles.split(",") if r.strip()]
    mgr       = ACLManager()
    mgr.load()
    mgr.add_client(client_name, role_list)
    console.print(f"Updated '{client_name}' → {role_list}")
