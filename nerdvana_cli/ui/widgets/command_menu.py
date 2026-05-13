"""CommandMenu — popup OptionList for slash-command autocomplete."""

from __future__ import annotations

from textual.widgets import OptionList
from textual.widgets.option_list import Option

SLASH_COMMANDS = [
    ("/help", "Show help"),
    ("/clear", "Clear chat"),
    ("/init", "Generate NIRNA.md"),
    ("/model", "Show/change model"),
    ("/models", "List available models"),
    ("/provider", "Add/switch provider"),
    ("/mode", "Activate/deactivate mode profile"),
    ("/context", "Set context profile"),
    ("/mcp", "MCP server status"),
    ("/tokens", "Show token usage"),
    ("/skills", "List available skills"),
    ("/tools", "List tools"),
    ("/update", "Check and install updates"),
    ("/memories", "List project memories"),
    ("/undo", "Restore pre-edit git checkpoint"),
    ("/redo", "Re-apply last undone checkpoint"),
    ("/checkpoints", "List session checkpoints"),
    ("/route-knowledge", "Classify content → suggest WriteMemory scope"),
    ("/dashboard", "Toggle observability dashboard"),
    ("/health", "Show 7-day tool call health summary"),
    ("/thinking", "Toggle inline thinking display (on/off)"),
    ("/activity", "Toggle activity indicator (on/off)"),
    ("/quit", "Exit"),
]


class CommandMenu(OptionList):
    """Popup menu for slash commands."""

    DEFAULT_CSS = """
    CommandMenu {
        layer: overlay;
        dock: bottom;
        height: auto;
        max-height: 10;
        margin: 0 0 3 0;
        border: tall $accent;
        background: $surface;
        display: none;
    }
    CommandMenu.visible {
        display: block;
    }
    """

    def on_mount(self) -> None:
        for cmd, desc in SLASH_COMMANDS:
            self.add_option(Option(f"{cmd}  {desc}", id=cmd))
