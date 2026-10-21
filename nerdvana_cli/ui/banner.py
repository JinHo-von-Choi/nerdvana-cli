"""Logo banner of the Textual TUI: the NerdVana art with the active model, context window and tool count.

작성자: 최진호
작성일: 2026-10-03
"""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.widgets import Static

from nerdvana_cli import __version__
from nerdvana_cli.core.config.settings import NerdvanaSettings

# The logos, widest first; the banner shows the widest one that fits its width.
_FULL_LOGO = (
    "███╗   ██╗███████╗██████╗ ██████╗ ██╗   ██╗ █████╗ ███╗   ██╗ █████╗ ",
    "████╗  ██║██╔════╝██╔══██╗██╔══██╗██║   ██║██╔══██╗████╗  ██║██╔══██╗",
    "██╔██╗ ██║█████╗  ██████╔╝██║  ██║██║   ██║███████║██╔██╗ ██║███████║",
    "██║╚██╗██║██╔══╝  ██╔══██╗██║  ██║╚██╗ ██╔╝██╔══██║██║╚██╗██║██╔══██║",
    "██║ ╚████║███████╗██║  ██║██████╔╝ ╚████╔╝ ██║  ██║██║ ╚████║██║  ██║",
    "╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝╚═════╝   ╚═══╝  ╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝",
)
_SMALL_LOGO = (
    " _  _            ___   __              ",
    "| \\| |___ _ _ __| \\ \\ / /_ _ _ _  __ _ ",
    "| .` / -_) '_/ _` |\\ V / _` | ' \\/ _` |",
    "|_|\\_\\___|_| \\__,_| \\_/\\__,_|_||_\\__,_|",
)
_TEXT_LOGO = ("NerdVana CLI",)
LOGOS = (_FULL_LOGO, _SMALL_LOGO, _TEXT_LOGO)


def logo_for(width: int) -> tuple[str, ...]:
    """The widest logo whose lines fit in *width* columns; the full one when the width is not known (0)."""
    if width <= 0:
        return _FULL_LOGO
    return next((logo for logo in LOGOS if max(len(line) for line in logo) <= width), _TEXT_LOGO)


def build_banner(settings: NerdvanaSettings, tool_count: int, parism: bool, width: int = 0) -> Text:
    """Render the banner for the current provider/model, context window and tool count.

    The logo is chosen to fit *width*, and no line wraps: a line longer than the banner is cut
    with an ellipsis instead of breaking the art across lines.
    """
    ctx_k = settings.session.max_context_tokens // 1000
    ctx_display = f"{ctx_k}K" if ctx_k < 1000 else f"{ctx_k // 1000}M"
    info = (
        f"v{__version__} | {settings.model.provider}/{settings.model.model} | ctx:{ctx_display} | Tools: {tool_count}"
        + (" | Parism" if parism else "")
    )
    banner = Text(no_wrap=True, overflow="ellipsis", justify="center")
    banner.append("\n".join(logo_for(width)) + "\n", style="bold bright_white")
    banner.append("https://nerdvana.kr | Feedback: jinho.von.choi@nerdvana.kr\n" + info, style="dim")
    return banner


class LogoBanner(Static):
    """The banner widget: it picks the logo again whenever its width changes."""

    _shown: tuple[NerdvanaSettings, int, bool] | None = None

    def show(self, settings: NerdvanaSettings, tool_count: int, parism: bool) -> None:
        """Render the banner for these values at the current width."""
        self._shown = (settings, tool_count, parism)
        self.update(build_banner(settings, tool_count, parism, self.size.width))

    def on_resize(self, event: events.Resize) -> None:
        if self._shown is not None:
            self.update(build_banner(*self._shown, event.size.width))
