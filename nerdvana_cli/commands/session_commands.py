"""Session and runtime-related command handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nerdvana_cli.core.analytics import AnalyticsReader

if TYPE_CHECKING:
    from nerdvana_cli.ui.app import NerdvanaApp


async def handle_clear(app: NerdvanaApp, args: str) -> None:
    """Handle /clear command — clear conversation history and chat UI."""
    if app._agent_loop:
        app._agent_loop.state.messages.clear()
        app._agent_loop.state.turn_count = 1
        app._agent_loop.reset_session()
        app._agent_loop.deactivate_skill()
    app._clear_chat_messages()
    app._add_chat_message("[dim]Conversation cleared.[/dim]")


async def handle_tokens(app: NerdvanaApp, args: str) -> None:
    """Handle /tokens command — show current token usage with accumulated cost."""
    if app._agent_loop:
        u = app._agent_loop.state.usage

        # Compute session cost from analytics DB
        cost_str = ""
        try:
            session_id = getattr(app._agent_loop.state, "session_id", "")
            if session_id:
                cost = AnalyticsReader().session_cost(session_id)
                cost_str = f" / [bold green]${cost:.4f}[/bold green]"
        except Exception:  # noqa: BLE001
            pass

        app._add_chat_message(
            f"[dim]Tokens: {u.input_tokens} in / {u.output_tokens} out / "
            f"{u.total_tokens} total{cost_str}[/dim]"
        )


async def handle_tools(app: NerdvanaApp, args: str) -> None:
    """Handle /tools command — list all registered tools."""
    if app._agent_loop:
        for t in app._agent_loop.registry.all_tools():
            safe = "R" if t.is_read_only else "W"
            app._add_chat_message(f"  [{safe}] [bold]{t.name}[/bold]")


async def handle_mcp(app: NerdvanaApp, args: str) -> None:
    """Handle /mcp command — show MCP server status."""
    if app.mcp_manager:
        status = app.mcp_manager.get_status()
        if not status:
            app._add_chat_message("[dim]No MCP servers configured.[/dim]")
        else:
            app._add_chat_message("[bold]MCP Servers:[/bold]")
            for name, connected in status.items():
                icon = "[green]ON[/green]" if connected else "[red]OFF[/red]"
                app._add_chat_message(f"  {icon} {name}")
    else:
        app._add_chat_message("[dim]No .mcp.json found.[/dim]")


async def handle_skills(app: NerdvanaApp, args: str) -> None:
    """Handle /skills command — list available skills."""
    if app._agent_loop:
        skills = app._agent_loop.skill_loader.list_skills()
        if skills:
            for s in skills:
                app._add_chat_message(
                    f"  [bold]{s.trigger}[/bold] -- {s.description}",
                    raw_text=f"{s.trigger} -- {s.description}",
                )
        else:
            app._add_chat_message(
                "[dim]No skills found. Add .md files to .nerdvana/skills/[/dim]"
            )


def show_session_context(app: NerdvanaApp, registry: Any) -> None:
    """Show session startup context summary."""
    from nerdvana_cli.core.nirnamd import load_nirna_files
    from nerdvana_cli.mcp.tools import McpToolAdapter

    parts = []

    # Working directory
    parts.append(f"  cwd: {app.settings.cwd}")

    # NIRNA.md status
    nirna_files = load_nirna_files(cwd=app.settings.cwd)
    if nirna_files:
        for nf in nirna_files:
            parts.append(f"  NIRNA.md ({nf.type}): {nf.path}")
    else:
        parts.append("  NIRNA.md: not found")

    # Tools summary
    tools = registry.all_tools()
    mcp = [t for t in tools if isinstance(t, McpToolAdapter)]
    builtin = [t for t in tools if not isinstance(t, McpToolAdapter)]
    parts.append(f"  Tools: {len(builtin)} built-in, {len(mcp)} MCP")

    # Skills
    if app._agent_loop:
        skills = app._agent_loop.skill_loader.list_skills()
        if skills:
            triggers = ", ".join(s.trigger for s in skills)
            parts.append(f"  Skills: {triggers}")

    # Context window
    ctx_k = app.settings.session.max_context_tokens // 1000
    parts.append(f"  Context: {ctx_k}K tokens")

    summary = "\n".join(parts)
    app._add_chat_message(
        f"[dim]Session initialized:\n{summary}[/dim]",
        raw_text=f"Session initialized:\n{summary}",
    )
