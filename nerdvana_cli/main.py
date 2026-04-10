"""CLI entry point — Typer-based command interface."""

from __future__ import annotations

import asyncio
import os
import sys

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from nerdvana_cli import __version__
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.session import SessionStorage
from nerdvana_cli.core.settings import NerdvanaSettings
from nerdvana_cli.tools.registry import create_tool_registry

app = typer.Typer(
    name="nerdvana",
    help="NerdVana CLI — AI-powered development tool. Supports 12 AI platforms.",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()


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
):
    """NerdVana CLI — AI-powered development tool.

    Supports 12 AI platforms: Anthropic, OpenAI, Gemini, Groq, OpenRouter, xAI,
    Ollama, vLLM, DeepSeek, Mistral, Cohere, Together AI.

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
        asyncio.run(
            repl_loop(
                config_path=config or None,
                cwd=cwd or os.getcwd(),
                verbose=verbose,
                model=model or None,
                provider=provider or None,
                max_tokens=max_tokens or None,
            )
        )


async def repl_loop(
    config_path: str | None = None,
    cwd: str = ".",
    verbose: bool = False,
    model: str | None = None,
    provider: str | None = None,
    max_tokens: int | None = None,
):
    """Interactive REPL loop."""
    settings = NerdvanaSettings.load(config_path)
    settings.cwd = cwd
    settings.verbose = verbose

    if model:
        settings.model.model = model
    if provider:
        settings.model.provider = provider
    if max_tokens:
        settings.model.max_tokens = max_tokens

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


async def handle_command(
    cmd: str,
    loop: AgentLoop,
    settings: NerdvanaSettings,
    session: SessionStorage,
    registry,
) -> bool:
    """Handle slash commands. Returns False to exit REPL."""
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if command in ("/quit", "/exit", "/q"):
        console.print("[dim]Goodbye![/dim]")
        return False

    elif command == "/help":
        console.print(
            Panel(
                "[bold]Commands[/bold]\n"
                "/help          — Show this help\n"
                "/quit          — Exit REPL\n"
                "/setup         — Run setup wizard\n"
                "/model         — Show current model\n"
                "/model <name>  — Change model\n"
                "/provider      — Show current provider\n"
                "/provider <p>  — Change provider\n"
                "/tokens        — Show token usage\n"
                "/clear         — Clear conversation\n"
                "/session       — Show session info\n"
                "/tools         — List available tools\n"
                "/providers     — List all supported providers\n"
                "/verbose       — Toggle verbose mode",
                title="Help",
            )
        )

    elif command == "/setup":
        from nerdvana_cli.core.setup import run_setup

        run_setup(force=True)

    elif command == "/model":
        if args:
            from nerdvana_cli.providers import detect_provider

            settings.model.model = args
            settings.model.provider = detect_provider(args).value
            loop.provider = loop.create_provider_from_settings()
            console.print(f"[dim]Model: {args} (provider: {settings.model.provider})[/dim]")
        else:
            console.print(f"[dim]Model: {settings.model.model} (provider: {settings.model.provider})[/dim]")

    elif command == "/provider":
        if args:
            from nerdvana_cli.providers import ProviderName

            try:
                settings.model.provider = args
                prov = ProviderName(args)
                from nerdvana_cli.providers.base import DEFAULT_MODELS

                settings.model.model = DEFAULT_MODELS.get(prov, settings.model.model)
                loop.provider = loop.create_provider_from_settings()
                console.print(f"[dim]Provider: {args} — model: {settings.model.model}[/dim]")
            except ValueError:
                console.print(f"[red]Unknown provider: {args}[/red]")
                console.print("[dim]Use /providers to see available providers.[/dim]")
        else:
            console.print(f"[dim]Provider: {settings.model.provider}[/dim]")

    elif command == "/tokens":
        usage = loop.state.usage
        console.print(
            f"[dim]Input: {usage.input_tokens} | Output: {usage.output_tokens} | Total: {usage.total_tokens}[/dim]"
        )

    elif command == "/clear":
        loop.state.messages.clear()
        loop.state.turn_count = 1
        console.print("[dim]Conversation cleared.[/dim]")

    elif command == "/session":
        console.print(f"[dim]Session ID: {session.session_id}[/dim]")
        console.print(f"[dim]Messages: {len(loop.state.messages)}[/dim]")
        console.print(f"[dim]Turns: {loop.state.turn_count}[/dim]")

    elif command == "/tools":
        tools = registry.all_tools()
        console.print("[bold]Available Tools:[/bold]")
        for t in tools:
            safe = "[green]R[/green]" if t.is_read_only else "[yellow]W[/yellow]"
            concurrent = "[green]||[/green]" if t.is_concurrency_safe else "[dim]|[/dim]"
            console.print(f"  {safe} {concurrent} [bold]{t.name}[/bold] — {t.description_text[:60]}...")

    elif command == "/providers":
        from nerdvana_cli.providers import print_providers_table

        print_providers_table()

    elif command == "/verbose":
        settings.verbose = not settings.verbose
        console.print(f"[dim]Verbose: {settings.verbose}[/dim]")

    else:
        console.print(f"[red]Unknown command: {command}[/red]")

    return True


@app.command()
def run(
    prompt: str = typer.Argument(..., help="Prompt to run"),
    config: str = typer.Option("", "--config", "-c", help="Config file path"),
    cwd: str = typer.Option("", "--cwd", help="Working directory"),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose output"),
    model: str = typer.Option("", "--model", "-m", help="Model name"),
    provider: str = typer.Option("", "--provider", "-p", help="AI provider"),
    max_tokens: int = typer.Option(0, "--max-tokens", help="Max tokens"),
):
    """Run a single prompt non-interactively."""
    settings = NerdvanaSettings.load(config or None)
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
    from nerdvana_cli.core.team       import TeamRegistry

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

    async def _run():
        async for chunk in loop.run(prompt):
            console.print(chunk, end="")
        console.print()

    asyncio.run(_run())


@app.command()
def setup(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing config"),
):
    """Interactive setup — choose provider, enter API key, select model."""
    from nerdvana_cli.core.setup import run_setup

    run_setup(force=force)


@app.command()
def providers():
    """List all supported AI providers."""
    from nerdvana_cli.providers import print_providers_table

    print_providers_table()


@app.command()
def version():
    """Show version."""
    console.print(f"NerdVana CLI v{__version__}")


if __name__ == "__main__":
    app()
