"""CLI entry point — Typer-based command interface."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.prompt import Prompt

from nerdvana_cli import __version__
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.migrate import run_if_needed as _migrate_run
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.tools.registry import create_tool_registry

app = typer.Typer(
    name="nerdvana",
    help="NerdVana CLI — AI-powered development tool. Supports 13 AI platforms.",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()


def _run_migration_once() -> None:
    """Run one-shot data migration from legacy locations to ~/.nerdvana/.

    Called once on startup, right after settings are loaded. Any failure is
    caught and logged — never blocks CLI startup.
    """
    try:
        if _migrate_run():
            console.print("[dim]Migrated user data to ~/.nerdvana/ (one-time)[/dim]")
    except Exception as e:
        console.print(f"[yellow]Migration warning: {e}[/yellow]")


# Approval-mode → (mode, trust_level) mapping (Codex-style, Phase F §6.3)
_APPROVAL_MODE_MAP: dict[str, tuple[str, str]] = {
    "default":   ("interactive", "balanced"),
    "auto_edit": ("editing",     "balanced"),
    "yolo":      ("one-shot",    "yolo"),
    "plan":      ("planning",    "strict"),
}


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version"),
    config: str = typer.Option("", "--config", "-c", help="Config file path"),
    cwd: str = typer.Option("", "--cwd", help="Working directory"),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose output"),
    model: str = typer.Option("", "--model", "-m", help="Model name"),
    provider: str = typer.Option("", "--provider", "-p", help="AI provider"),
    max_tokens: int = typer.Option(0, "--max-tokens", help="Max tokens per response"),
    approval_mode: str = typer.Option(
        "",
        "--approval-mode",
        help="Preset mode: default | auto_edit | yolo | plan",
    ),
) -> None:
    """NerdVana CLI — AI-powered development tool.

    Supports 13 AI platforms: Anthropic, OpenAI, Gemini, Groq, OpenRouter, xAI,
    Ollama, vLLM, DeepSeek, Mistral, Cohere, Together AI, ZAI.

    Run without subcommands to start interactive REPL mode.
    First run triggers interactive setup wizard.
    """
    if version:
        console.print(f"[bold]NerdVana CLI[/bold] v{__version__}")
        try:
            import asyncio as _asyncio

            from nerdvana_cli.core.updater import check_for_update
            result = _asyncio.run(check_for_update(__version__))
            if result:
                console.print(f"[yellow]Update available: {result['version']}[/yellow]")
                console.print("[dim]Run: nerdvana update  or  /update in REPL[/dim]")
        except Exception:
            pass
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        # Suppress "Event loop is closed" warnings from subprocess GC
        _original_unraisablehook = sys.unraisablehook

        def _quiet_unraisable(unraisable: sys.UnraisableHookArgs) -> None:
            if unraisable.exc_type is RuntimeError and "Event loop is closed" in str(unraisable.exc_value):
                return
            _original_unraisablehook(unraisable)

        sys.unraisablehook = _quiet_unraisable

        # --approval-mode shorthand → default_mode override applied in repl_loop
        resolved_approval = approval_mode.strip().lower() if approval_mode else ""

        asyncio.run(
            repl_loop(
                config_path    = config or None,
                cwd            = cwd or os.getcwd(),
                verbose        = verbose,
                model          = model or None,
                provider       = provider or None,
                max_tokens     = max_tokens or None,
                approval_mode  = resolved_approval or None,
            )
        )


async def repl_loop(
    config_path:   str | None = None,
    cwd:           str        = ".",
    verbose:       bool       = False,
    model:         str | None = None,
    provider:      str | None = None,
    max_tokens:    int | None = None,
    approval_mode: str | None = None,
) -> None:
    """Interactive REPL loop."""
    settings = NerdvanaSettings.load(config_path)
    _run_migration_once()
    settings.cwd     = cwd
    settings.verbose = verbose

    if model:
        settings.model.model = model
    if provider:
        settings.model.provider = provider
    if max_tokens:
        settings.model.max_tokens = max_tokens

    # --approval-mode → default_mode override (Phase F §6.3)
    if approval_mode:
        mapped_mode, _ = _APPROVAL_MODE_MAP.get(approval_mode, (approval_mode, "balanced"))
        settings.session.default_mode = mapped_mode

    # Auto-run setup if no config and no API key
    from nerdvana_cli.core.setup import has_config_file, has_valid_api_key, run_setup
    from nerdvana_cli.providers import ProviderName, detect_provider
    from nerdvana_cli.providers.factory import resolve_api_key

    if not config_path and not has_config_file() and not has_valid_api_key():
        config = run_setup()
        if config:
            settings = NerdvanaSettings.load()

    # Resolve provider
    if not settings.model.provider:
        prov = detect_provider(settings.model.model)
        settings.model.provider = prov.value
    else:
        prov = ProviderName(settings.model.provider)

    if not settings.model.api_key:
        settings.model.api_key = resolve_api_key(prov)

    if not settings.model.api_key and prov not in (ProviderName.OLLAMA, ProviderName.VLLM):
        console.print(f"[bold red]No API key found for {prov.value}.[/bold red]")
        console.print()
        if Prompt.ask("Run setup wizard?", choices=["y", "n"], default="y") == "y":
            from nerdvana_cli.core.setup import run_setup

            config = run_setup()
            if config:
                settings = NerdvanaSettings.load()
        else:
            console.print("Set the API key via environment variable or config file.")
            raise typer.Exit(1)

    # Parism lifecycle
    parism_client = None
    if settings.parism.enabled:
        from nerdvana_cli.tools.parism_client import ParismClient
        if ParismClient.is_available():
            parism_client = ParismClient(cwd=cwd)
            try:
                await parism_client.connect()
                console.print("[dim]Parism connected.[/dim]")
            except Exception as e:
                console.print(f"[dim yellow]Parism unavailable: {e}[/dim yellow]")
                parism_client = None
                if not settings.parism.fallback_to_bash:
                    console.print("[red]Parism required but unavailable. Exiting.[/red]")
                    raise typer.Exit(1) from None

    # MCP servers lifecycle
    mcp_manager = None
    from nerdvana_cli.mcp.config import load_mcp_config
    mcp_configs = load_mcp_config(cwd=cwd)
    if mcp_configs:
        from nerdvana_cli.mcp.manager import McpManager
        mcp_manager = McpManager(mcp_configs)
        mcp_status = await mcp_manager.connect_all()
        for name, st in mcp_status.items():
            if st.startswith("connected"):
                console.print(f"[dim]MCP {name}: {st}[/dim]")
            else:
                console.print(f"[dim yellow]MCP {name}: {st}[/dim yellow]")

    # Launch TUI
    from nerdvana_cli.ui.app import NerdvanaApp

    tui_app = NerdvanaApp(settings=settings, parism_client=parism_client, mcp_manager=mcp_manager)
    try:
        await tui_app.run_async()
    finally:
        if mcp_manager:
            await mcp_manager.disconnect_all()
        if parism_client:
            await parism_client.disconnect()


@app.command()
def run(
    prompt: str = typer.Argument(..., help="Prompt to run"),
    config: str = typer.Option("", "--config", "-c", help="Config file path"),
    cwd: str = typer.Option("", "--cwd", help="Working directory"),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose output"),
    model: str = typer.Option("", "--model", "-m", help="Model name"),
    provider: str = typer.Option("", "--provider", "-p", help="AI provider"),
    max_tokens: int = typer.Option(0, "--max-tokens", help="Max tokens"),
) -> None:
    """Run a single prompt non-interactively."""
    settings = NerdvanaSettings.load(config or None)
    _run_migration_once()
    settings.cwd = cwd or os.getcwd()
    settings.verbose = verbose
    if model:
        settings.model.model = model
    if provider:
        settings.model.provider = provider
    if max_tokens:
        settings.model.max_tokens = max_tokens

    from nerdvana_cli.providers import ProviderName, detect_provider
    from nerdvana_cli.providers.factory import resolve_api_key

    if not settings.model.provider:
        prov = detect_provider(settings.model.model)
        settings.model.provider = prov.value
    else:
        prov = ProviderName(settings.model.provider)

    if not settings.model.api_key:
        settings.model.api_key = resolve_api_key(prov)

    if not settings.model.api_key and prov not in (ProviderName.OLLAMA, ProviderName.VLLM):
        console.print(f"[red]Error: No API key found for {prov.value}.[/red]")
        raise typer.Exit(1)

    from nerdvana_cli.core.task_state import TaskRegistry
    from nerdvana_cli.core.team import TeamRegistry

    task_registry = TaskRegistry()
    team_registry = TeamRegistry()
    registry      = create_tool_registry(
        settings      = settings,
        task_registry = task_registry,
        team_registry = team_registry,
    )
    session = SessionStorage()
    loop    = AgentLoop(
        settings      = settings,
        registry      = registry,
        session       = session,
        task_registry = task_registry,
        team_registry = team_registry,
    )

    async def _run() -> None:
        async for chunk in loop.run(prompt):
            console.print(chunk, end="")
        console.print()

    asyncio.run(_run())


@app.command()
def setup(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing config"),
) -> None:
    """Interactive setup — choose provider, enter API key, select model."""
    from nerdvana_cli.core.setup import run_setup

    run_setup(force=force)


@app.command()
def providers() -> None:
    """List all supported AI providers."""
    from nerdvana_cli.providers import print_providers_table

    print_providers_table()


@app.command()
def version() -> None:
    """Show version."""
    console.print(f"NerdVana CLI v{__version__}")


# ---------------------------------------------------------------------------
# nerdvana serve — Phase G1
# ---------------------------------------------------------------------------


@app.command()
def serve(
    transport:   str  = typer.Option("stdio",     "--transport",   help="Transport: stdio or http"),
    port:        int  = typer.Option(10830,        "--port",        help="HTTP listen port (≥10000)"),
    host:        str  = typer.Option("127.0.0.1", "--host",        help="HTTP bind address"),
    allow_write: bool = typer.Option(False,        "--allow-write", help="Enable write tools"),
    tls_cert:    str  = typer.Option("",           "--tls-cert",    help="TLS certificate file (PEM)"),
    tls_ca:      str  = typer.Option("",           "--tls-ca",      help="CA certificate for mTLS"),
    project:     str  = typer.Option("",           "--project",     help="Project root directory (Phase H)"),
    mode:        str  = typer.Option("",           "--mode",        help="Profile mode name to activate (Phase H)"),
) -> None:
    """Start NerdVana as an MCP 1.0 server.

    External harnesses (Claude Code, Cursor, Continue …) can call
    nerdvana tools via the mcp__nerdvana__* namespace.

    Examples:
        nerdvana serve                                              # stdio (default)
        nerdvana serve --transport http --port 10830
        nerdvana serve --transport http --allow-write
        nerdvana serve --project /path/to/lib --mode query         # Phase H external query
    """
    from pathlib import Path as _Path

    from nerdvana_cli.server.mcp_server import NerdvanaMcpServer

    if transport not in ("stdio", "http"):
        console.print(f"[red]Error: unknown transport '{transport}'. Use 'stdio' or 'http'.[/red]")
        raise typer.Exit(1)

    if transport == "http" and port < 10000:
        console.print(f"[red]Error: port {port} is below 10000. Use a port ≥ 10000.[/red]")
        raise typer.Exit(1)

    # Phase H: resolve project path (defaults to cwd when not specified).
    project_path: _Path | None = None
    if project:
        project_path = _Path(project).expanduser().resolve()
        if not project_path.is_dir():
            console.print(f"[red]Error: --project path does not exist: {project_path}[/red]")
            raise typer.Exit(1)

    server = NerdvanaMcpServer(
        allow_write  = allow_write,
        transport    = transport,
        host         = host,
        port         = port,
        tls_cert     = _Path(tls_cert) if tls_cert else None,
        tls_ca       = _Path(tls_ca)   if tls_ca  else None,
        project_path = project_path,
        mode         = mode or None,
    )

    if transport == "http":
        console.print(
            f"[bold]NerdVana MCP server[/bold] listening on "
            f"http://{host}:{port}/mcp  "
            f"[{'write' if allow_write else 'read-only'}]",
            file=sys.stderr,
        )
    else:
        console.print(
            f"[bold]NerdVana MCP server[/bold] running on stdio  "
            f"[{'write' if allow_write else 'read-only'}]",
            file=sys.stderr,
        )

    asyncio.run(server.run())


# ---------------------------------------------------------------------------
# nerdvana hook — hook bridge sub-group — Phase G2
# ---------------------------------------------------------------------------

hook_app = typer.Typer(
    name           = "hook",
    help           = "Dispatch Claude Code / Codex / VSCode hook JSON via stdin/stdout.",
    add_completion = False,
)
app.add_typer(hook_app)


@hook_app.command("pre-tool-use")
def hook_pre_tool_use(
    db: str = typer.Option("", "--db", help="Path to audit.sqlite (default: ~/.nerdvana/audit.sqlite)"),
) -> None:
    """Handle a pre-tool-use hook: read JSON from stdin, write response to stdout."""
    from nerdvana_cli.server.hook_bridge import run_hook

    db_path = Path(db) if db else None
    raise typer.Exit(run_hook("pre-tool-use", db_path=db_path))


@hook_app.command("post-tool-use")
def hook_post_tool_use(
    db: str = typer.Option("", "--db", help="Path to audit.sqlite (default: ~/.nerdvana/audit.sqlite)"),
) -> None:
    """Handle a post-tool-use hook: read JSON from stdin, write response to stdout."""
    from nerdvana_cli.server.hook_bridge import run_hook

    db_path = Path(db) if db else None
    raise typer.Exit(run_hook("post-tool-use", db_path=db_path))


@hook_app.command("prompt-submit")
def hook_prompt_submit(
    db: str = typer.Option("", "--db", help="Path to audit.sqlite (default: ~/.nerdvana/audit.sqlite)"),
) -> None:
    """Handle a prompt-submit hook: read JSON from stdin, write response to stdout."""
    from nerdvana_cli.server.hook_bridge import run_hook

    db_path = Path(db) if db else None
    raise typer.Exit(run_hook("prompt-submit", db_path=db_path))


@hook_app.command("list")
def hook_list() -> None:
    """List supported hook types."""
    from nerdvana_cli.server.hook_schemas import HOOK_NAMES

    console.print("[bold]Supported hooks:[/bold]")
    for name in sorted(HOOK_NAMES):
        console.print(f"  {name}")


# ---------------------------------------------------------------------------
# nerdvana admin — ACL sub-group — Phase G1
# ---------------------------------------------------------------------------

admin_app = typer.Typer(
    name    = "admin",
    help    = "Administrative commands.",
    add_completion = False,
)
app.add_typer(admin_app)

acl_app = typer.Typer(
    name    = "acl",
    help    = "Manage MCP access-control list (mcp_acl.yml).",
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


if __name__ == "__main__":
    app()
