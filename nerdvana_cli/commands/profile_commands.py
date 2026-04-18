"""Profile-related slash-command handlers — /mode and /context.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nerdvana_cli.core.profiles import ProfileManager
    from nerdvana_cli.ui.app import NerdvanaApp


async def handle_mode(app: NerdvanaApp, args: str) -> None:
    """Handle /mode [<name> | list | off] command.

    /mode list     — show available modes
    /mode off      — pop the top mode (revert to previous)
    /mode <name>   — activate the named mode
    /mode          — show current mode
    """

    pm: ProfileManager = _get_profile_manager(app)
    token = args.strip().lower()

    if not token or token == "status":
        _show_current(app, pm)
        return

    if token == "list":
        names = pm.available_modes()
        lines = ["Available modes:"] + [f"  {n}" for n in names]
        app._add_chat_message("\n".join(lines))
        return

    if token == "off":
        removed = pm.pop_mode()
        if removed:
            app._add_chat_message(f"Mode deactivated: {removed}. Current: {pm.active_mode_name}")
        else:
            app._add_chat_message(f"No mode to deactivate. Current: {pm.active_mode_name}")
        return

    try:
        profile = pm.push_mode(token)
        app._add_chat_message(
            f"Mode activated: {profile.name} (trust={profile.trust_level})\n"
            f"{profile.description}"
        )
    except ValueError as exc:
        app._add_chat_message(f"[red]Error: {exc}[/red]")


async def handle_context(app: NerdvanaApp, args: str) -> None:
    """Handle /context [<name> | list] command.

    /context list   — show available contexts
    /context <name> — activate the named context
    /context        — show current context
    """

    pm: ProfileManager = _get_profile_manager(app)
    token = args.strip().lower()

    if not token or token == "status":
        _show_current(app, pm)
        return

    if token == "list":
        names = pm.available_contexts()
        lines = ["Available contexts:"] + [f"  {n}" for n in names]
        app._add_chat_message("\n".join(lines))
        return

    try:
        profile = pm.set_context(token)
        app._add_chat_message(
            f"Context activated: {profile.name}\n"
            f"{profile.description}"
        )
    except ValueError as exc:
        app._add_chat_message(f"[red]Error: {exc}[/red]")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_profile_manager(app: NerdvanaApp) -> ProfileManager:
    """Return the ProfileManager from the app, creating one if needed."""
    from nerdvana_cli.core.profiles import ProfileManager

    # Dynamic attribute — NerdvanaApp is a TUI class we cannot easily annotate here.
    _app: Any = app  # noqa: ANN401  (intentional escape hatch)
    if not hasattr(_app, "_profile_manager") or _app._profile_manager is None:
        _app._profile_manager = ProfileManager(cwd=app.settings.cwd)
        # Apply defaults from settings
        dm = app.settings.session.default_mode
        dc = app.settings.session.default_context
        if dc != "standalone":
            with contextlib.suppress(ValueError):
                _app._profile_manager.set_context(dc)
        if dm != "interactive":
            with contextlib.suppress(ValueError):
                _app._profile_manager.set_mode(dm)
    pm: ProfileManager = _app._profile_manager
    return pm


def _show_current(app: NerdvanaApp, pm: ProfileManager) -> None:
    summary = pm.current_config_summary()
    lines   = [
        f"Context : {summary['context']}",
        f"Mode    : {summary['mode']}",
        f"Stack   : {summary['mode_stack']}",
        f"Trust   : {summary['trust_level']}",
    ]
    if summary["model_override"]:
        lines.append(f"Model   : {summary['model_override']}")
    if summary["excluded_tools"]:
        lines.append(f"Excluded: {', '.join(summary['excluded_tools'])}")
    app._add_chat_message("\n".join(lines))
