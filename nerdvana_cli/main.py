"""CLI entry point — Typer-based command interface."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

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
from nerdvana_cli.core.settings import NerdvanaSettings, SettingsLoadError
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


def _load_settings(config_path: str | None = None) -> NerdvanaSettings:
    """Load settings, turning a rejected security setting into a clean exit."""
    try:
        return NerdvanaSettings.load(config_path)
    except SettingsLoadError as exc:
        console_stderr.print(f"[bold red]Invalid configuration:[/bold red] {exc}")
        raise typer.Exit(2) from None


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


def _run_migration_once(out: Console | None = None) -> None:
    """Run one-shot data migration from legacy locations to ~/.nerdvana/.

    Called once on startup, right after settings are loaded. Any failure is
    caught and logged — never blocks CLI startup.
    """
    try:
        if _migrate_run():
            (out or console).print("[dim]Migrated user data to ~/.nerdvana/ (one-time)[/dim]")
    except Exception as e:
        (out or console).print(f"[yellow]Migration warning: {e}[/yellow]")


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
    settings = _load_settings(config_path)
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
            settings = _load_settings()

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
                settings = _load_settings()
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


def _apply_run_overrides(settings: NerdvanaSettings, overrides: dict[str, Any], assignments: list[str]) -> None:
    """Set each ``section.field`` of *settings* from a command-line value, skipping the unset ones.

    Unset means empty, zero or False, the options' defaults. *assignments* are the ``--set`` strings;
    one that cannot be applied ends the command with the configuration exit code.
    """
    from nerdvana_cli.core.run_output import EXIT_CONFIG
    from nerdvana_cli.core.settings import apply_settings_overrides

    for dotted, value in overrides.items():
        if value:
            section, field = dotted.split(".")
            setattr(getattr(settings, section), field, value)
    try:
        apply_settings_overrides(settings, assignments)
    except ValueError as exc:
        console_stderr.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(EXIT_CONFIG) from exc


def _receipt_of(loop: Any, verification: dict[str, Any] | None) -> dict[str, Any] | None:
    """The receipt of a run, or None when it changed nothing and checked nothing."""
    from nerdvana_cli.core.analytics import AnalyticsReader
    from nerdvana_cli.core.receipt import build_receipt

    receipt = build_receipt(loop, verification, AnalyticsReader().cost_breakdown(loop.session.session_id))
    return receipt if receipt["files_changed"] or verification else None


def _fill_outcome(outcome: Any, loop: Any, duration_ms: int) -> None:
    """Copy what the finished loop measured into the run result."""
    outcome.turns        = loop.turns_used
    outcome.cost_usd     = loop.session_cost_usd()
    outcome.usage        = loop.usage_summary()
    outcome.signals      = loop.signal_summary()
    outcome.verification = loop.verification_summary()
    outcome.receipt      = _receipt_of(loop, outcome.verification)
    outcome.duration_ms  = duration_ms


def _resolve_run_provider(settings: NerdvanaSettings) -> tuple[str, bool]:
    """Fill in the provider and its API key from the model name and the environment.

    Returns the provider name and whether a key is still missing (local providers need none).
    """
    from nerdvana_cli.providers import ProviderName, detect_provider
    from nerdvana_cli.providers.factory import resolve_api_key

    if not settings.model.provider:
        settings.model.provider = detect_provider(settings.model.model).value
    prov = ProviderName(settings.model.provider)
    if not settings.model.api_key:
        settings.model.api_key = resolve_api_key(prov)
    return prov.value, not settings.model.api_key and prov not in (ProviderName.OLLAMA, ProviderName.VLLM)


@app.command()
def run(
    prompt: str = typer.Argument(..., help="Prompt to run"),
    config: str = typer.Option("", "--config", "-c", help="Config file path"),
    cwd: str = typer.Option("", "--cwd", help="Working directory"),
    verbose: bool = typer.Option(False, "--verbose", help="Verbose output"),
    model: str = typer.Option("", "--model", "-m", help="Model name"),
    provider: str = typer.Option("", "--provider", "-p", help="AI provider"),
    max_tokens: int = typer.Option(0, "--max-tokens", help="Max tokens"),
    output_format: str = typer.Option(
        "text",
        "--output-format",
        help="text (default), json (one result object at the end) or stream-json (one event per line)",
    ),
    max_turns: int = typer.Option(0, "--max-turns", help="Stop after this many model turns (0 = config value)"),
    max_cost_usd: float = typer.Option(0.0, "--max-cost-usd", help="Stop once the estimated cost reaches this many USD (0 = no limit)"),
    approval_mode: str = typer.Option("", "--approval-mode", help="Preset mode: default | auto_edit | yolo | plan"),
    max_total_tokens: int = typer.Option(0, "--max-total-tokens", help="Stop once input plus output tokens of all requests reach this many (0 = no limit)"),
    require_price: bool = typer.Option(False, "--require-price", help="Refuse to run when --max-cost-usd is set but the model has no known price"),
    sandbox: str = typer.Option("", "--sandbox", help="Confine shell commands to a write scope: off | auto | require (default: sandbox.mode from the configuration)"),
    verify: str = typer.Option("", "--verify", help="Command that decides whether the task is done: the run goes on until it exits with status 0"),
    verify_attempts: int = typer.Option(0, "--verify-attempts", help="Failed verifications before giving up (0 = goal.max_attempts)"),
    scope: list[str] | None = typer.Option(None, "--scope", help="With --verify: paths the task is about; an edit elsewhere is refused unless someone approves it"),  # noqa: B008
    set_values: list[str] | None = typer.Option(None, "--set", help="Override one setting for this run: section.field=value (repeatable), e.g. --set session.compact_threshold=0.5"),  # noqa: B008
) -> None:
    """Run a single prompt non-interactively.

    Exit codes: 0 success, 1 the run failed, 2 invalid configuration or options,
    3 a turn or cost limit stopped the run.
    """
    import time

    from nerdvana_cli.core.goal import Goal
    from nerdvana_cli.core.run_output import EXIT_CONFIG, FORMATS, RunReporter, RunResult
    from nerdvana_cli.core.sandbox import MODES as SANDBOX_MODES

    resolved_approval = approval_mode.strip().lower()
    for flag, value, allowed in (
        ("--output-format", output_format,     FORMATS),
        ("--sandbox",       sandbox,           SANDBOX_MODES),
        ("--approval-mode", resolved_approval, tuple(_APPROVAL_MODE_MAP)),
    ):
        if value and value not in allowed:
            console_stderr.print(f"[red]Error: {flag} must be one of {', '.join(allowed)}.[/red]")
            raise typer.Exit(EXIT_CONFIG)

    def _write(text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    reporter = RunReporter(output_format, _write, console)
    outcome  = RunResult()

    settings = _load_settings(config or None)
    for warning in settings.load_warnings:
        console_stderr.print(f"[dim yellow]Config: {warning.format()}[/dim yellow]")
    _run_migration_once(console_stderr if reporter.machine_readable else None)
    settings.cwd = cwd or os.getcwd()
    settings.verbose = verbose
    _apply_run_overrides(settings, {
        "model.model":              model,
        "model.provider":           provider,
        "model.max_tokens":         max_tokens,
        "session.max_turns":        max_turns,
        "session.max_cost_usd":     max_cost_usd,
        "session.max_total_tokens": max_total_tokens,
        "session.require_price":    require_price,
        "sandbox.mode":             sandbox,
        "session.default_mode":     _APPROVAL_MODE_MAP[resolved_approval][0] if resolved_approval else "",
    }, set_values or [])

    prov, key_missing = _resolve_run_provider(settings)
    outcome.provider = settings.model.provider
    outcome.model    = settings.model.model
    if key_missing:
        outcome.stop = "config"
        reporter.failure(outcome, f"No API key found for {prov}.")
        raise typer.Exit(EXIT_CONFIG)

    from nerdvana_cli.core.task_state import TaskRegistry

    task_registry = TaskRegistry()
    registry      = create_tool_registry(
        settings      = settings,
        task_registry = task_registry,
    )
    session = SessionStorage(persist=settings.session.persist)
    loop    = AgentLoop(
        settings      = settings,
        registry      = registry,
        session       = session,
        task_registry = task_registry,
    )
    outcome.session_id = session.session_id
    started            = time.monotonic()
    if verify:
        loop.set_goal(Goal(objective=prompt, verify=verify, max_attempts=verify_attempts or settings.goal.max_attempts, scope=list(scope or [])))

    async def _run() -> None:
        loop.usage_listener = reporter.request
        reporter.start(session.session_id, settings.model.provider, settings.model.model)
        try:
            async for chunk in loop.run(prompt):
                reporter.chunk(chunk)
        except Exception as exc:  # noqa: BLE001
            if not reporter.machine_readable:
                raise
            outcome.stop  = "error"
            outcome.error = f"{type(exc).__name__}: {exc}"
        else:
            outcome.stop = loop.last_stop
        _fill_outcome(outcome, loop, int((time.monotonic() - started) * 1000))
        reporter.finish(outcome)

    try:
        asyncio.run(_run())
    finally:
        loop.close_session("exit")
    if outcome.exit_code:
        raise typer.Exit(outcome.exit_code)


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
    by:          str  = typer.Option("provider", "--by",    help="Group by: provider | model | agent | category | tool"),
) -> None:
    """Aggregate token usage and USD cost over a time window."""
    from nerdvana_cli.commands.cost_command import cost_command

    cost_command(since=since, json_output=json_output, by=by)


@app.command()
def approvals(
    since:       int  = typer.Option(30, "--since", help="Look back this many days"),
    min_count:   int  = typer.Option(3,  "--min",   help="Approvals needed before a call is suggested"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> None:
    """Suggest always_allow rules for permission questions you keep approving (nothing is written)."""
    from nerdvana_cli.commands.approvals_command import approvals_command

    approvals_command(since=since, min_approvals=min_count, json_output=json_output)


@app.command()
def review(
    base:          str  = typer.Option("HEAD", "--base", help="Review the working tree against this git ref"),
    path:          list[str] | None = typer.Option(None, "--path", help="Limit the review to this path (repeatable)"),  # noqa: B008
    context_only:  bool = typer.Option(False, "--context-only", help="Print what the reviewer would be given and stop; calls no model"),
    output_format: str  = typer.Option("text", "--output-format", help="text or json"),
    fail_on:       str  = typer.Option("never", "--fail-on", help="Exit 1 when a finding has at least this severity: low | medium | high | never"),
    model:         str  = typer.Option("", "--model", "-m", help="Model name"),
    provider:      str  = typer.Option("", "--provider", "-p", help="AI provider"),
) -> None:
    """Review a change with a read-only agent that starts from the changed code and the places that use it."""
    from nerdvana_cli.commands.review_command import FAIL_ON, review_command

    if fail_on not in FAIL_ON or output_format not in ("text", "json"):
        console_stderr.print(f"[red]Error: --fail-on must be one of {', '.join(FAIL_ON)} and --output-format text or json.[/red]")
        raise typer.Exit(2)
    code = review_command(base, path or [], context_only, output_format, fail_on, model, provider)
    if code:
        raise typer.Exit(code)


if __name__ == "__main__":
    app()
