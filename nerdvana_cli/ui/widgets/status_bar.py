"""StatusBar — bottom status bar showing model, tokens, and session info."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import Static


class StatusBar(Static):
    """Bottom status bar showing model, tokens, session info."""

    # The output of session.statusline_command, shown last; empty without one.
    extra: str = ""
    _last: dict[str, Any] | None = None

    def set_extra(self, text: str) -> None:
        """Show *text* at the end of the bar and redraw it with the last values."""
        self.extra = text
        self.update_status(**(self._last or {}))

    def update_status(
        self,
        model: str    = "",
        provider: str = "",
        tokens_in: int  = 0,
        tokens_out: int = 0,
        tools: int = 0,
        parism: bool = False,
        thinking: bool = False,
        elapsed_s: float = 0.0,
        cache_read: int = 0,
    ) -> None:
        self._last = {
            "model": model, "provider": provider, "tokens_in": tokens_in, "tokens_out": tokens_out, "tools": tools,
            "parism": parism, "thinking": thinking, "elapsed_s": elapsed_s, "cache_read": cache_read,
        }
        parts: list[str] = []
        if thinking:
            elapsed_str = f"{elapsed_s:.1f}s" if elapsed_s < 60 else f"{elapsed_s / 60:.1f}m"
            token_str = ""
            if tokens_in or tokens_out:
                token_str = f" | {tokens_in + tokens_out} tokens"
            parts.append(f"thinking ({elapsed_str}{token_str})")
        if provider and model:
            parts.append(f"{provider}/{model}")
        if not thinking and (tokens_in or tokens_out):
            parts.append(f"tokens: {tokens_in} in / {tokens_out} out")
            if tokens_in and cache_read:
                parts.append(f"cache: {min(100, round(100 * cache_read / tokens_in))}%")
        if tools:
            tool_text = f"tools: {tools}"
            if parism:
                tool_text += " (Parism)"
            parts.append(tool_text)
        if self.extra:
            parts.append(self.extra)
        self.update(Text(" | ".join(parts) if parts else "Ready"))
