"""CLI entry point — Typer-based command interface."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import typer
from rich.console import Console

from nerdvana_cli import __version__
from nerdvana_cli.acp.command import acp_command
from nerdvana_cli.cli.bootstrap import ExecutionProfile, build_agent_loop
from nerdvana_cli.cli.runtime import (
    APPROVAL_MODE_MAP,
    console,
    console_stderr,
    enforce_managed_policy,
    load_settings,
    repl_loop,
    resolve_run_provider,
    run_migration_once,
)
from nerdvana_cli.commands.admin_command import admin_app
from nerdvana_cli.commands.agents_command import agents_app
from nerdvana_cli.commands.history_command import history_app
from nerdvana_cli.commands.hook_command import hook_app
from nerdvana_cli.commands.mcp_command import mcp_app
from nerdvana_cli.commands.memory_command import memory_app
from nerdvana_cli.commands.schedule_command import schedule_app
from nerdvana_cli.commands.session_command import session_app
from nerdvana_cli.commands.skill_command import skill_app
from nerdvana_cli.commands.workflow_command import workflow_app
from nerdvana_cli.core.agent_loop import AgentLoop
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.session import SessionStorage, resume_session_id
from nerdvana_cli.core.telemetry.telemetry_otel import chain_usage_listeners
from nerdvana_cli.providers.base import ProviderName

app = typer.Typer(
    name="nerdvana",
    help="NerdVana CLI — AI-powered development tool. Supports 21 AI platforms.",
    add_completion=False,
    rich_markup_mode="rich",
)
app.add_typer(session_app, name="session")
app.add_typer(history_app, name="history")
app.add_typer(mcp_app,     name="mcp")
app.add_typer(skill_app,   name="skill")
app.add_typer(memory_app,  name="memory")
app.add_typer(hook_app)
app.add_typer(admin_app)
app.command(name="acp")(acp_command)
app.add_typer(schedule_app)
app.add_typer(workflow_app)
app.add_typer(agents_app)


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

        from nerdvana_cli.cli.updater import (
            cached_or_check,
            format_update_notice,
            is_update_check_enabled,
        )
        from nerdvana_cli.core.config.settings import NerdvanaSettings

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


def _apply_run_overrides(settings: NerdvanaSettings, overrides: dict[str, Any], assignments: list[str]) -> None:
    """Set each ``section.field`` of *settings* from a command-line value, skipping the unset ones.

    Unset means empty, zero or False, the options' defaults. *assignments* are the ``--set`` strings;
    one that cannot be applied ends the command with the configuration exit code.
    """
    from nerdvana_cli.cli.run_output import EXIT_CONFIG
    from nerdvana_cli.core.config.settings import apply_settings_overrides

    for dotted, value in overrides.items():
        if value:
            section, field = dotted.split(".")
            setattr(getattr(settings, section), field, value)
    try:
        apply_settings_overrides(settings, assignments)
    except ValueError as exc:
        console_stderr.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(EXIT_CONFIG) from exc
    enforce_managed_policy(settings)


def _receipt_of(loop: Any, verification: dict[str, Any] | None) -> dict[str, Any] | None:
    """The receipt of a run, or None when it changed nothing and checked nothing."""
    from nerdvana_cli.cli.receipt import build_receipt
    from nerdvana_cli.core.telemetry.analytics import AnalyticsReader

    receipt = build_receipt(loop, verification, AnalyticsReader().cost_breakdown(loop.session.session_id))
    return receipt if receipt["files_changed"] or verification else None


def _load_run_images(paths: list[str], cwd: str, reporter: Any, outcome: Any) -> list[dict[str, Any]]:
    """The image blocks named by ``--image``; a file that cannot be sent ends the command with the configuration exit code."""
    from nerdvana_cli.cli.run_output import EXIT_CONFIG
    from nerdvana_cli.core.images import ImageError, load_images

    try:
        return load_images(paths, cwd)
    except ImageError as exc:
        outcome.stop = "config"
        reporter.failure(outcome, str(exc))
        raise typer.Exit(EXIT_CONFIG) from exc


def _fill_outcome(outcome: Any, loop: Any, duration_ms: int) -> None:
    """Copy what the finished loop measured into the run result."""
    outcome.provider     = loop.settings.model.provider
    outcome.model        = loop.settings.model.model
    outcome.turns        = loop.turns_used
    outcome.cost_usd     = loop.total_cost_usd()
    outcome.usage        = loop.usage_summary()
    outcome.signals      = loop.signal_summary()
    outcome.verification = loop.verification_summary()
    outcome.receipt      = _receipt_of(loop, outcome.verification)
    outcome.duration_ms  = duration_ms


def _start_run_loop(settings: NerdvanaSettings, resume: str, reporter: Any, outcome: Any) -> tuple[SessionStorage, AgentLoop]:
    """The session and agent loop of a run: a new session, or with ``--resume`` the recorded one with its conversation restored."""
    from nerdvana_cli.cli.run_output import EXIT_CONFIG
    from nerdvana_cli.core.task_state import TaskRegistry

    resumed = resume_session_id(resume) if resume else None
    session = SessionStorage(session_id=resumed, persist=settings.session.persist)
    if resume and not (resumed and session.load_messages()):
        outcome.stop = "config"
        reporter.failure(outcome, f"Cannot resume session '{resume}': no recorded conversation.")
        raise typer.Exit(EXIT_CONFIG)
    loop = build_agent_loop(settings, ExecutionProfile(session=session, task_registry=TaskRegistry()))
    if resumed:
        loop.restore_history()
    return session, loop


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
    image: list[str] | None = typer.Option(None, "--image", help="Attach an image (PNG, JPEG, GIF or WebP) to the prompt (repeatable)"),  # noqa: B008
    scope: list[str] | None = typer.Option(None, "--scope", help="With --verify: paths the task is about; an edit elsewhere is refused unless someone approves it"),  # noqa: B008
    set_values: list[str] | None = typer.Option(None, "--set", help="Override one setting for this run: section.field=value (repeatable), e.g. --set session.compact_threshold=0.5"),  # noqa: B008
    resume: str = typer.Option("", "--resume", help="Continue the recorded conversation of this session id; the prompt is the next message"),
) -> None:
    """Run a single prompt non-interactively.

    Exit codes: 0 success, 1 the run failed, 2 invalid configuration or options,
    3 a turn or cost limit stopped the run.
    """
    import time

    from nerdvana_cli.cli.run_output import EXIT_CONFIG, FORMATS, RunReporter, RunResult
    from nerdvana_cli.core.config.settings_sections import SANDBOX_MODES
    from nerdvana_cli.core.goal import Goal

    resolved_approval = approval_mode.strip().lower()
    for flag, value, allowed in (
        ("--output-format", output_format,     FORMATS),
        ("--sandbox",       sandbox,           SANDBOX_MODES),
        ("--approval-mode", resolved_approval, tuple(APPROVAL_MODE_MAP)),
    ):
        if value and value not in allowed:
            console_stderr.print(f"[red]Error: {flag} must be one of {', '.join(allowed)}.[/red]")
            raise typer.Exit(EXIT_CONFIG)

    def _write(text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    reporter = RunReporter(output_format, _write, console)
    outcome  = RunResult()

    settings = load_settings(config or None)
    for warning in settings.load_warnings:
        console_stderr.print(f"[dim yellow]Config: {warning.format()}[/dim yellow]")
    run_migration_once(console_stderr if reporter.machine_readable else None)
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
        "session.default_mode":     APPROVAL_MODE_MAP[resolved_approval][0] if resolved_approval else "",
    }, set_values or [])

    prov, key_missing = resolve_run_provider(settings)
    outcome.provider = settings.model.provider
    outcome.model    = settings.model.model
    if key_missing:
        outcome.stop = "config"
        reporter.failure(outcome, f"No API key found for {prov}.")
        raise typer.Exit(EXIT_CONFIG)

    session, loop = _start_run_loop(settings, resume, reporter, outcome)
    outcome.session_id = session.session_id
    started            = time.monotonic()
    images             = _load_run_images(image or [], settings.cwd, reporter, outcome)
    if verify:
        loop.set_goal(Goal(objective=prompt, verify=verify, max_attempts=verify_attempts or settings.goal.max_attempts, scope=list(scope or [])))

    async def _run() -> None:
        loop.usage_listener = chain_usage_listeners(loop.usage_listener, reporter.request)
        reporter.start(session.session_id, settings.model.provider, settings.model.model)
        try:
            async for chunk in loop.run(prompt, images):
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
    from nerdvana_cli.cli.setup import run_setup

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
# nerdvana serve
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
    project:     str  = typer.Option("",           "--project",     help="Project root directory"),
    mode:        str  = typer.Option("",           "--mode",        help="Profile mode name to activate"),
) -> None:
    """Start NerdVana as an MCP 1.0 server.

    External harnesses (Claude Code, Cursor, Continue …) can call
    nerdvana tools via the mcp__nerdvana__* namespace.

    Examples:
        nerdvana serve                                              # stdio (default)
        nerdvana serve --transport http --port 10830
        nerdvana serve --transport http --allow-write
        nerdvana serve --transport http --tls-cert server.crt --tls-key server.key
        nerdvana serve --project /path/to/lib --mode query         # external query
    """
    from pathlib import Path as _Path

    from nerdvana_cli.server.mcp_server import NerdvanaMcpServer, TlsConfigurationError

    if transport not in ("stdio", "http"):
        console.print(f"[red]Error: unknown transport '{transport}'. Use 'stdio' or 'http'.[/red]")
        raise typer.Exit(1)

    if transport == "http" and port < 10000:
        console.print(f"[red]Error: port {port} is below 10000. Use a port ≥ 10000.[/red]")
        raise typer.Exit(1)

    # Resolve project path (defaults to cwd when not specified).
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
def context(
    session_id:  str  = typer.Argument("", help="Stored session id (default: the most recent session)"),
    top:         int  = typer.Option(8, "--top", help="Largest tools and tool results to list"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> None:
    """Show where the context window goes in a stored session: system prompt, tool declarations, messages, tool results."""
    from nerdvana_cli.commands.context_command import context_command

    code = context_command(session_id, top, json_output)
    if code:
        raise typer.Exit(code)


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


@app.command(name="import")
def import_(
    source:  str  = typer.Argument(..., help="claude or codex"),
    write:   bool = typer.Option(False, "--write", help="Copy the commands (without it, only show what would happen)"),
    project: str  = typer.Option(".", "--project", help="The project directory"),
) -> None:
    """Bring over slash commands and permission rules from Claude Code or Codex (the rules are printed, not applied)."""
    from nerdvana_cli.commands.import_command import SOURCES, import_command

    if source not in SOURCES:
        console_stderr.print(f"[red]Error: source must be one of {', '.join(SOURCES)}.[/red]")
        raise typer.Exit(2)
    print(import_command(source, write, project))


if __name__ == "__main__":
    app()
