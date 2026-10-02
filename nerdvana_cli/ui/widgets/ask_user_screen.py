"""AskUserScreen: modal prompt behind the AskUser tool."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class AskUserScreen(ModalScreen[str | None]):
    """Show a question with numbered options and a free-text field.

    Dismisses with the chosen option text, the typed free text, or ``None`` when
    the user cancels with Escape. Typing a bare option number selects that option.
    """

    DEFAULT_CSS = """
    AskUserScreen {
        align: center middle;
    }
    AskUserScreen > Vertical {
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 80%;
        border: tall $accent;
        background: $surface;
        padding: 1 2;
    }
    AskUserScreen #ask-user-question {
        margin-bottom: 1;
    }
    AskUserScreen #ask-user-hint {
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Dismiss")]

    def __init__(self, question: str, options: list[str]) -> None:
        super().__init__()
        self._question = question
        self._options  = options

    def compose(self) -> ComposeResult:
        """Lay out the question, the numbered options, and the free-text input."""
        with Vertical():
            yield Static(self._question, id="ask-user-question", markup=False)
            if self._options:
                yield OptionList(
                    *[Option(f"{i}. {text}", id=str(i)) for i, text in enumerate(self._options, start=1)],
                    id="ask-user-options",
                )
            yield Input(placeholder="Type an answer or an option number", id="ask-user-input")
            yield Static("Enter to submit, Escape to dismiss", id="ask-user-hint")

    def on_mount(self) -> None:
        """Focus the options when present, otherwise the free-text field."""
        target = "#ask-user-options" if self._options else "#ask-user-input"
        self.query_one(target).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Answer with the selected option's text."""
        event.stop()
        self.dismiss(self._options[event.option_index])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Answer with the typed text, resolving a bare option number."""
        event.stop()
        self.dismiss(self.resolve_answer(event.value, self._options))

    def action_cancel(self) -> None:
        """Dismiss without an answer."""
        self.dismiss(None)

    @staticmethod
    def resolve_answer(raw: str, options: list[str]) -> str | None:
        """Map typed text to an answer: a bare option number selects that option."""
        text = raw.strip()
        if not text:
            return None
        if text.isdigit() and 1 <= int(text) <= len(options):
            return options[int(text) - 1]
        return text
