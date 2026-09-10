"""CLI entry point — Typer-based command interface."""

from __future__ import annotations

import asyncio
import os
import sys

import typer
from rich.console import Console
from rich.prompt import Prompt

from nerdvana_cli import __version__
from nerdvana_cli.commands.admin_command import admin_app
from nerdvana_cli.commands.hook_command import hook_app
from nerdvana_cli.commands.mcp_command import mcp_app
from nerdvana_cli.commands.memory_command import memory_app
from nerdvana_cli.commands.session_command import session_app
from nerdvana_cli.commands.skill_command import skill_app
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.migrate import run_if_needed as _migrate_run
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.providers.base import ProviderName
from nerdvana_cli.tools.registry import create_tool_registry

app = typer.Typer(
    name="nerdvana",
    help="NerdVana CLI — AI-powered development tool. Supports 21 AI platforms.",
    add_completion=False,
    rich_markup_mode="rich",
)
app.add_typer(session_app, name="session")
app.add_typer(mcp_app,     name="mcp")
app.add_typer(skill_app,   name="skill")
app.add_typer(memory_app,  name="memory")
app.add_typer(hook_app)
app.add_typer(admin_app)
console        = Console()
console_stderr = Console(stderr=True)


def _maybe_show_update_notice(target: Console | None = None) -> None:
    """Print a single dim line if a newer release is available.

    Uses a 24-hour cache and a 5s HTTP timeout. Silent on every failure mode
    (offline, rate-limit, malformed cache). Suppressed when
    `NERDVANA_NO_UPDATE_CHECK=1` or `session.update_check` is False.

    Args:
        target: Console to print on. Defaults to stdout, which is correct only
            while stdout carries human-readable output. Callers that hand
            stdout to a machine-readable protocol pass ``console_stderr``.
    """
    out = target if target is not None else console
    try:
        import asyncio as _asyncio

        from nerdvana_cli.core.settings import NerdvanaSettings
        from nerdvana_cli.core.updater import (
            cached_or_check,
            format_update_notice,
            is_update_check_enabled,
        )

        try:
            _flag = bool(NerdvanaSettings().session.update_check)
        except Exception:
            _flag = True
        if not is_update_check_enabled(_flag):
            return

        result = _asyncio.run(cached_or_check(__version__))
        if result and result.get("version"):
            out.print(
                format_update_notice(__version__, result["version"], result.get("url", "")),
                highlight=False,
            )
    except Exception:
        # Never let a startup notice abort the CLI.
        pass


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


# Provider names shown in --help are derived from the enum the CLI validates
# against, so the help text cannot drift away from what --provider accepts.
_SUPPORTED_PROVIDERS: tuple[str, ...] = tuple(p.value for p in ProviderName)

_MAIN_HELP: str = (
    "NerdVana CLI, an AI-powered development tool.\n\n"
    f"Supported providers ({len(_SUPPORTED_PROVIDERS)}): "
    f"{', '.join(_SUPPORTED_PROVIDERS)}.\n\n"
    "Run without subcommands to start interactive REPL mode. "
    "First run triggers the interactive setup wizard."
)


@app.callback(invoke_without_command=True, help=_MAIN_HELP)
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
    no_update_check: bool = typer.Option(
        False,
        "--no-update-check",
        help="Skip the startup new-version check for this run.",
    ),
) -> None:
    """Root callback.

    The user-facing text, including the provider list, is supplied through
    ``_MAIN_HELP`` so that it is generated from :class:`ProviderName`.
    """
    if no_update_check:
        os.environ["NERDVANA_NO_UPDATE_CHECK"] = "1"

    if version:
        console.print(f"[bold]NerdVana CLI[/bold] v{__version__}")
        _maybe_show_update_notice()
        raise typer.Exit()

    if ctx.invoked_subcommand is not None:
        # A subcommand owns stdout from here on, and `serve --transport stdio`
        # speaks JSON-RPC there: a notice printed on stdout would corrupt the
        # first frame. Subcommands therefore get the notice on stderr.
        _maybe_show_update_notice(console_stderr)
        return

    _maybe_show_update_notice()

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
    tls_key:     str  = typer.Option("",           "--tls-key",     help="TLS private key file (PEM); omit only when the certificate bundles it"),
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
        nerdvana serve --transport http --tls-cert server.crt --tls-key server.key
        nerdvana serve --project /path/to/lib --mode query         # Phase H external query
    """
    from pathlib import Path as _Path

    from nerdvana_cli.server.mcp_server import NerdvanaMcpServer, TlsConfigurationError

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

    try:
        server = NerdvanaMcpServer(
            allow_write  = allow_write,
            transport    = transport,
            host         = host,
            port         = port,
            tls_cert     = _Path(tls_cert) if tls_cert else None,
            tls_key      = _Path(tls_key)  if tls_key  else None,
            tls_ca       = _Path(tls_ca)   if tls_ca  else None,
            project_path = project_path,
            mode         = mode or None,
        )
    except TlsConfigurationError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc

    if transport == "http":
        console_stderr.print(
            f"[bold]NerdVana MCP server[/bold] listening on "
            f"http://{host}:{port}/mcp  "
            f"[{'write' if allow_write else 'read-only'}]",
        )
    else:
        console_stderr.print(
            f"[bold]NerdVana MCP server[/bold] running on stdio  "
            f"[{'write' if allow_write else 'read-only'}]",
        )

    asyncio.run(server.run())


# ---------------------------------------------------------------------------
# nerdvana doctor — installation/env diagnostics
# ---------------------------------------------------------------------------


@app.command()
def doctor(
    strict:      bool = typer.Option(False, "--strict", help="Treat warnings as failures"),
    json_output: bool = typer.Option(False, "--json",   help="Machine-readable JSON output"),
) -> None:
    """Diagnose installation, keys, and external dependencies."""
    from nerdvana_cli.commands.doctor_command import doctor_command

    doctor_command(strict=strict, json_output=json_output)


@app.command()
def cost(
    since:       str  = typer.Option("7d",      "--since", help="Time window (e.g. 7d, 30d, 24h, all)"),
    json_output: bool = typer.Option(False,      "--json",  help="Machine-readable JSON output"),
    by:          str  = typer.Option("provider", "--by",    help="Group by: provider | model"),
) -> None:
    """Aggregate token usage and USD cost over a time window."""
    from nerdvana_cli.commands.cost_command import cost_command

    cost_command(since=since, json_output=json_output, by=by)


if __name__ == "__main__":
    app()
