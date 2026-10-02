"""ConfirmScreen: modal asking the user to allow or deny a tool call."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmScreen(ModalScreen[bool]):
    """Show what is about to run and ask for a decision.

    Dismisses with True only when the user explicitly allows (the Allow button
    or ``y``). Deny, ``n``, Escape and any other way out dismiss with False, and
    the Deny button has the initial focus so a stray Enter refuses.
    """

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    ConfirmScreen > Vertical {
        width: 80%;
        max-width: 110;
        height: auto;
        max-height: 85%;
        border: tall $warning;
        background: $surface;
        padding: 1 2;
    }
    ConfirmScreen #confirm-title {
        text-style: bold;
        margin-bottom: 1;
    }
    ConfirmScreen #confirm-body {
        height: auto;
        max-height: 24;
        margin-bottom: 1;
    }
    ConfirmScreen Horizontal {
        height: auto;
        align-horizontal: right;
    }
    ConfirmScreen Button {
        margin-left: 2;
    }
    """

    BINDINGS = [
        Binding("y", "allow", "Allow"),
        Binding("n", "deny", "Deny"),
        Binding("escape", "deny", "Deny"),
    ]

    def __init__(self, tool_name: str, message: str) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._message   = message

    def compose(self) -> ComposeResult:
        """Lay out the tool name, the reason or change, and the two choices."""
        with Vertical():
            yield Static(f"Allow {self._tool_name}?", id="confirm-title", markup=False)
            with VerticalScroll(id="confirm-body"):
                yield Static(self._message, id="confirm-message", markup=False)
            with Horizontal():
                yield Button("Allow (y)", id="confirm-allow", variant="success")
                yield Button("Deny (n)", id="confirm-deny", variant="error")

    def on_mount(self) -> None:
        """Focus Deny so that pressing Enter without reading refuses."""
        self.query_one("#confirm-deny", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dismiss according to the pressed button."""
        event.stop()
        self.dismiss(event.button.id == "confirm-allow")

    def action_allow(self) -> None:
        """Allow the call."""
        self.dismiss(True)

    def action_deny(self) -> None:
        """Refuse the call."""
        self.dismiss(False)
