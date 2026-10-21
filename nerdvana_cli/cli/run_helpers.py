"""The helpers of the ``nerdvana run`` command.

The command itself stays in ``main.py``; the steps it is made of live here, so
that neither the entry point nor the command has to grow past its share of the
file.

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import typer

from nerdvana_cli.cli.bootstrap import ExecutionProfile, build_agent_loop
from nerdvana_cli.cli.runtime import console, console_stderr, enforce_managed_policy
from nerdvana_cli.core.config.settings import NerdvanaSettings
from nerdvana_cli.core.loop.agent_loop import AgentLoop
from nerdvana_cli.core.state.session import SessionStorage, resume_session_id


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


def _validate_run_flags(checks: Sequence[tuple[str, str, tuple[str, ...]]]) -> None:
    """Refuse a flag whose value the command does not know.

    Each check is the name of a flag, the value it was given and the values it
    accepts. An empty value is the option's default and is always accepted.
    """
    from nerdvana_cli.cli.run_output import EXIT_CONFIG

    for flag, value, accepted in checks:
        if value and value not in accepted:
            console_stderr.print(f"[red]Error: {flag} must be one of {', '.join(accepted)}.[/red]")
            raise typer.Exit(EXIT_CONFIG)


def _receipt_of(loop: Any, verification: dict[str, Any] | None) -> dict[str, Any] | None:
    """The receipt of a run, or None when it changed nothing and checked nothing."""
    from nerdvana_cli.cli.receipt import build_receipt
    from nerdvana_cli.core.telemetry.analytics import AnalyticsReader

    receipt = build_receipt(loop, verification, AnalyticsReader().cost_breakdown(loop.session.session_id))
    return receipt if receipt["files_changed"] or verification else None


def _load_run_images(paths: list[str], cwd: str, reporter: Any, outcome: Any) -> list[dict[str, Any]]:
    """The image blocks named by ``--image``; a file that cannot be sent ends the command with the configuration exit code."""
    from nerdvana_cli.cli.run_output import EXIT_CONFIG
    from nerdvana_cli.core.loop.images import ImageError, load_images

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
    outcome.price_known  = loop.limits.price_known()
    outcome.usage        = loop.usage_summary()
    outcome.signals      = loop.signal_summary()
    outcome.verification = loop.verification_summary()
    outcome.receipt      = _receipt_of(loop, outcome.verification)
    outcome.duration_ms  = duration_ms


def _start_run_loop(settings: NerdvanaSettings, resume: str, reporter: Any, outcome: Any) -> tuple[SessionStorage, AgentLoop]:
    """The session and agent loop of a run: a new session, or with ``--resume`` the recorded one with its conversation restored."""
    from nerdvana_cli.cli.run_output import EXIT_CONFIG
    from nerdvana_cli.core.delegation.task_state import TaskRegistry

    resumed = resume_session_id(resume) if resume else None
    session = SessionStorage(session_id=resumed, persist=settings.session.persist)
    if resume and not (resumed and session.load_messages()):
        outcome.stop = "config"
        reporter.failure(outcome, f"Cannot resume session '{resume}': no recorded conversation.")
        raise typer.Exit(EXIT_CONFIG)
    if resumed and session.orphaned_compaction_lock():
        # A compaction that never finished: the history it was rewriting is still in the transcript.
        console.print(f"[yellow]Warning: session '{resumed}' was interrupted during a compaction; its history may be half rewritten.[/yellow]")
    loop = build_agent_loop(settings, ExecutionProfile(session=session, task_registry=TaskRegistry()))
    if resumed:
        loop.restore_history()
    return session, loop
