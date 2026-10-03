"""Startup steps shared by the entry point and the command modules.

The interactive REPL, the settings loader and the provider resolution live here
so that ``main`` and the ``commands`` modules can both use them without
importing each other.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.prompt import Prompt

from nerdvana_cli.core.migrate import run_if_needed as _migrate_run
from nerdvana_cli.core.settings import NerdvanaSettings, SettingsLoadError
from nerdvana_cli.core.telemetry_otel import setup as setup_tracing

console        = Console()
console_stderr = Console(stderr=True)

# Approval-mode → (mode, trust_level) mapping (Codex-style)
APPROVAL_MODE_MAP: dict[str, tuple[str, str]] = {
    "default":   ("interactive", "balanced"),
    "auto_edit": ("editing",     "balanced"),
    "yolo":      ("one-shot",    "yolo"),
    "plan":      ("planning",    "strict"),
}


def load_settings(config_path: str | None = None) -> NerdvanaSettings:
    """Load settings, turning a rejected security setting into a clean exit."""
    try:
        return NerdvanaSettings.load(config_path)
    except SettingsLoadError as exc:
        console_stderr.print(f"[bold red]Invalid configuration:[/bold red] {exc}")
        raise typer.Exit(2) from None


def run_migration_once(out: Console | None = None) -> None:
    """Run one-shot data migration from legacy locations to ~/.nerdvana/.

    Called once on startup, right after settings are loaded. Any failure is
    caught and logged, and never blocks CLI startup.
    """
    try:
        if _migrate_run():
            (out or console).print("[dim]Migrated user data to ~/.nerdvana/ (one-time)[/dim]")
    except Exception as e:
        (out or console).print(f"[yellow]Migration warning: {e}[/yellow]")


def resolve_run_provider(settings: NerdvanaSettings) -> tuple[str, bool]:
    """Fill in the provider and its API key from the model name and the environment.

    Returns the provider name and whether a key is still missing (local providers need none).
    This is the step every command that runs an agent takes before building one, so it is also
    where OpenTelemetry tracing is switched on when ``telemetry.otel`` asks for it; a notice on
    stderr says why it stays off when it cannot start.
    """
    notice = setup_tracing(settings)
    if notice:
        console_stderr.print(f"[dim yellow]{notice}[/dim yellow]")
    from nerdvana_cli.providers import ProviderName, detect_provider
    from nerdvana_cli.providers.factory import resolve_api_key

    if not settings.model.provider:
        settings.model.provider = detect_provider(settings.model.model).value
    prov = ProviderName(settings.model.provider)
    if not settings.model.api_key:
        settings.model.api_key = resolve_api_key(prov)
    return prov.value, not settings.model.api_key and prov not in (ProviderName.OLLAMA, ProviderName.VLLM)


def _apply_launch_options(
    settings:      NerdvanaSettings,
    model:         str | None,
    provider:      str | None,
    max_tokens:    int | None,
    approval_mode: str | None,
) -> None:
    """Override the loaded settings with the options given on the command line."""
    if model:
        settings.model.model = model
    if provider:
        settings.model.provider = provider
    if max_tokens:
        settings.model.max_tokens = max_tokens
    if approval_mode:
        mapped_mode, _ = APPROVAL_MODE_MAP.get(approval_mode, (approval_mode, "balanced"))
        settings.session.default_mode = mapped_mode


async def repl_loop(
    config_path:   str | None = None,
    cwd:           str        = ".",
    verbose:       bool       = False,
    model:         str | None = None,
    provider:      str | None = None,
    max_tokens:    int | None = None,
    approval_mode: str | None = None,
    resume_id:     str | None = None,
) -> None:
    """Interactive REPL loop.

    ``resume_id`` names a session transcript to continue; without it the
    ``NERDVANA_RESUME`` environment variable is still honoured.
    """
    settings = load_settings(config_path)
    run_migration_once()
    settings.cwd     = cwd
    settings.verbose = verbose
    _apply_launch_options(settings, model, provider, max_tokens, approval_mode)

    # Auto-run setup if no config and no API key
    from nerdvana_cli.core.setup import has_config_file, has_valid_api_key, run_setup

    if not config_path and not has_config_file() and not has_valid_api_key():
        config = run_setup()
        if config:
            settings = load_settings()

    prov, key_missing = resolve_run_provider(settings)

    if key_missing:
        console.print(f"[bold red]No API key found for {prov}.[/bold red]")
        console.print()
        if Prompt.ask("Run setup wizard?", choices=["y", "n"], default="y") == "y":
            config = run_setup()
            if config:
                settings = load_settings()
        else:
            console.print("Set the API key via environment variable or config file.")
            raise typer.Exit(1)

    parism_client = await _connect_parism(settings, cwd)
    mcp_manager   = await _connect_mcp(cwd)

    # Launch TUI
    from nerdvana_cli.ui.app import NerdvanaApp

    tui_app = NerdvanaApp(settings=settings, parism_client=parism_client, mcp_manager=mcp_manager, resume_id=resume_id)
    try:
        await tui_app.run_async()
    finally:
        if mcp_manager:
            await mcp_manager.disconnect_all()
        if parism_client:
            await parism_client.disconnect()


async def _connect_parism(settings: NerdvanaSettings, cwd: str) -> Any:
    """Connect the Parism client when enabled; None when it is off or unavailable."""
    if not settings.parism.enabled:
        return None
    from nerdvana_cli.tools.parism_client import ParismClient

    if not ParismClient.is_available():
        return None
    client = ParismClient(cwd=cwd)
    try:
        await client.connect()
    except Exception as e:
        console.print(f"[dim yellow]Parism unavailable: {e}[/dim yellow]")
        if not settings.parism.fallback_to_bash:
            console.print("[red]Parism required but unavailable. Exiting.[/red]")
            raise typer.Exit(1) from None
        return None
    console.print("[dim]Parism connected.[/dim]")
    return client


async def _connect_mcp(cwd: str) -> Any:
    """Connect the configured MCP servers; None when none are configured."""
    from nerdvana_cli.mcp.config import load_mcp_config

    configs = load_mcp_config(cwd=cwd)
    if not configs:
        return None
    try:
        from nerdvana_cli.mcp.manager import McpManager
    except ImportError:
        console.print("[dim yellow]MCP servers are configured but the mcp package is not installed (pip install 'nerdvana-cli[mcp]').[/dim yellow]")
        return None

    manager = McpManager(configs)
    for name, status in (await manager.connect_all()).items():
        style = "dim" if status.startswith("connected") else "dim yellow"
        console.print(f"[{style}]MCP {name}: {status}[/{style}]")
    return manager
