"""StatusBar — bottom status bar showing model, tokens, and session info."""

from __future__ import annotations

from textual.widgets import Static


class StatusBar(Static):
    """Bottom status bar showing model, tokens, session info."""

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
    ) -> None:
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
        if tools:
            tool_text = f"tools: {tools}"
            if parism:
                tool_text += " (Parism)"
            parts.append(tool_text)
        self.update(" | ".join(parts) if parts else "Ready")
