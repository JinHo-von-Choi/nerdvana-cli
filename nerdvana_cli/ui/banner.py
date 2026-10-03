"""Logo banner of the Textual TUI: the NerdVana art with the active model, context window and tool count.

작성자: 최진호
작성일: 2026-10-03
"""

from __future__ import annotations

from rich.text import Text

from nerdvana_cli import __version__
from nerdvana_cli.core.settings import NerdvanaSettings


def build_banner(settings: NerdvanaSettings, tool_count: int, parism: bool) -> Text:
    """Render the banner for the current provider/model, context window and tool count."""
    ctx_k = settings.session.max_context_tokens // 1000
    ctx_display = f"{ctx_k}K" if ctx_k < 1000 else f"{ctx_k // 1000}M"
    return Text.from_markup(
        "[bold bright_white]"
        " ███╗   ██╗███████╗██████╗ ██████╗ ██╗   ██╗ █████╗ ███╗   ██╗ █████╗ \n"
        " ████╗  ██║██╔════╝██╔══██╗██╔══██╗██║   ██║██╔══██╗████╗  ██║██╔══██╗\n"
        " ██╔██╗ ██║█████╗  ██████╔╝██║  ██║██║   ██║███████║██╔██╗ ██║███████║\n"
        " ██║╚██╗██║██╔══╝  ██╔══██╗██║  ██║╚██╗ ██╔╝██╔══██║██║╚██╗██║██╔══██║\n"
        " ██║ ╚████║███████╗██║  ██║██████╔╝ ╚████╔╝ ██║  ██║██║ ╚████║██║  ██║\n"
        " ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝╚═════╝   ╚═══╝  ╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝\n"
        "[/bold bright_white]"
        "[dim]https://nerdvana.kr | Feedback: jinho.von.choi@nerdvana.kr\n"
        f"v{__version__} | {settings.model.provider}/{settings.model.model} | "
        f"ctx:{ctx_display} | Tools: {tool_count}"
        + (" | Parism" if parism else "")
        + "[/dim]"
    )
