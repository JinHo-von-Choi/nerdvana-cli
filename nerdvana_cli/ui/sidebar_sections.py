"""Section widgets for the responsive sidebar."""
from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.widget import Widget


_MAX_TOPIC_LEN = 30
_MAX_CWD_LEN   = 30


def _truncate(text: str, limit: int = _MAX_TOPIC_LEN) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _short_cwd(cwd: str, limit: int = _MAX_CWD_LEN) -> str:
    """Return the cwd as a short path preserving the final component.

    Examples: ~/…/nerdvana-cli, /tmp/…/mydir
    """
    p = Path(cwd).expanduser().resolve()
    home = Path.home()
    try:
        rel = "~/" + str(p.relative_to(home))
    except ValueError:
        rel = str(p)
    if len(rel) <= limit:
        return rel
    # Keep the basename and as much of the prefix as fits
    name = p.name
    prefix = rel[: limit - len(name) - 2]
    return f"{prefix}\u2026/{name}"


class SidebarContextSection(Widget):
    """Provider/model label + context-usage progress bar."""

    DEFAULT_CSS = """
    SidebarContextSection {
        height: auto;
        padding: 0 0 1 0;
        border-top: dashed $accent 30%;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._provider = ""
        self._model    = ""
        self._pct      = 0

    def set_state(self, provider: str, model: str, pct: int) -> None:
        self._provider = provider
        self._model    = model
        self._pct      = max(0, min(100, pct))
        self.refresh()

    def render(self) -> Text:
        out = Text()
        out.append("MODEL\n", style="bold")
        label = f"{self._provider}/{self._model}"
        out.append(_truncate(label) + "\n", style="cyan")
        bar_w  = 28
        filled = int(bar_w * self._pct / 100)
        color  = "green" if self._pct < 60 else "yellow" if self._pct < 80 else "red"
        out.append("\u2588" * filled, style=color)
        out.append("\u2591" * (bar_w - filled), style="dim")
        out.append(f" {self._pct}%", style=color)
        return out


class SidebarHeaderSection(Widget):
    """Top of sidebar: current session topic + cwd."""

    DEFAULT_CSS = """
    SidebarHeaderSection {
        height: auto;
        padding: 0 0 1 0;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._topic: str = "(new session)"
        self._cwd: str = str(Path.cwd())

    def set_state(self, topic: str, cwd: str) -> None:
        self._topic = topic or "(new session)"
        self._cwd = cwd
        self.refresh()

    def render(self) -> Text:
        out = Text()
        out.append("\u25cf ", style="bold cyan")
        out.append(_truncate(self._topic) + "\n", style="bold")
        out.append("  " + _short_cwd(self._cwd), style="dim")
        return out
