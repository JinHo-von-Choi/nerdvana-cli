"""Slash-command dispatcher for the Textual TUI.

Extracted from ``NerdvanaApp._handle_command``. The dispatcher receives the
App reference and routes ``/cmd args`` strings to the per-area handler modules
under ``nerdvana_cli.commands``. Adding a new slash command means appending
one row to ``_HANDLERS`` here — App needs no edits.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from textual.widgets import Input

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp


CommandHandler = Callable[["NerdvanaApp", str], Awaitable[None]]


def _build_handler_map() -> dict[str, CommandHandler]:
    """Lazy-import command modules and assemble the routing table.

    Imports are deferred so that importing this module does not pull the full
    command surface at process start. The map is rebuilt per dispatch — cheap
    (~µs) and keeps the dispatcher pure (no module-level mutable state).
    """
    from nerdvana_cli.commands import (
        memory_commands,
        model_commands,
        observability_commands,
        profile_commands,
        session_commands,
        system_commands,
    )

    return {
        "/model":           model_commands.handle_model,
        "/models":          model_commands.handle_models,
        "/provider":        model_commands.handle_provider,
        "/clear":           session_commands.handle_clear,
        "/tokens":          session_commands.handle_tokens,
        "/tools":           session_commands.handle_tools,
        "/mcp":             session_commands.handle_mcp,
        "/skills":          session_commands.handle_skills,
        "/help":            system_commands.handle_help,
        "/update":          system_commands.handle_update,
        "/init":            system_commands.handle_init,
        "/setup":           system_commands.handle_init,
        "/undo":            memory_commands.handle_undo,
        "/redo":            memory_commands.handle_redo,
        "/checkpoints":     memory_commands.handle_checkpoints,
        "/memories":        memory_commands.handle_memories,
        "/route-knowledge": memory_commands.handle_route_knowledge,
        "/mode":            profile_commands.handle_mode,
        "/context":         profile_commands.handle_context,
        "/health":          observability_commands.handle_health,
        "/dashboard":       observability_commands.handle_dashboard,
        "/thinking":        system_commands.handle_thinking,
        "/activity":        system_commands.handle_activity,
    }


_EXIT_ALIASES = frozenset({"/quit", "/exit", "/q"})


async def dispatch_command(app: NerdvanaApp, cmd: str) -> None:
    """Route a slash command to the appropriate handler.

    Resolution order:
        1. ``/quit`` aliases — exit the app.
        2. Static handler map — direct dispatch.
        3. Skill trigger fallback — activate matching skill and forward args.
        4. Unknown — print red error in chat.
    """
    parts   = cmd.split(maxsplit=1)
    command = parts[0].lower()
    args    = parts[1] if len(parts) > 1 else ""

    if command in _EXIT_ALIASES:
        app.exit()
        return

    handler = _build_handler_map().get(command)
    if handler:
        await handler(app, args)
        return

    # Skill trigger fallback
    if app._agent_loop:
        skill = app._agent_loop.skill_loader.get_by_trigger(command)
        if skill:
            app._agent_loop.activate_skill(skill.body)
            app._add_chat_message(
                f"[dim]Skill activated: {skill.name}[/dim]",
                raw_text=f"Skill activated: {skill.name}",
            )
            if args:
                input_widget = app.query_one("#user-input", Input)
                input_widget.value = args
                app.call_later(input_widget.action_submit)
            return

    app._add_chat_message(f"[red]Unknown command: {command}[/red]")
