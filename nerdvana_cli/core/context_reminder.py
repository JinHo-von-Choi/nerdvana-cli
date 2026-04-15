"""Per-turn context reminder and tool result ring buffer.

The reminder is injected as a synthetic user message right before the real
user prompt each turn. It is wrapped in <system-reminder>...</system-reminder>
so prompt-engineered models will treat it as harness context, not user intent.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RecentToolResult:
    name:         str
    args_summary: str
    preview:      str
    ok:           bool


class ContextReminder:
    """Owns the ring buffer of recent tool results and builds per-turn text."""

    def __init__(self, cwd: str, max_recent: int = 5) -> None:
        self._cwd:    str                               = cwd
        self._recent: deque[RecentToolResult]           = deque(maxlen=max_recent)

    def record_tool(self, result: RecentToolResult) -> None:
        self._recent.append(result)

    def build(self, turn: int) -> str:
        lines: list[str] = [
            "<system-reminder>",
            f"Harness context refresh — turn={turn}, cwd={self._cwd}",
        ]
        if self._recent:
            lines.append("")
            lines.append("Recent tool results:")
            for r in self._recent:
                icon       = "ok" if r.ok else "err"
                args_short = r.args_summary[:60]
                preview    = r.preview.replace("\n", " ")[:80]
                lines.append(f"  - {r.name}({args_short}) [{icon}] {preview}")
        lines.append("")
        lines.append(
            "Stay grounded in this context. Do not re-run tools you already ran "
            "unless state changed. Cite file:line for references."
        )
        lines.append("</system-reminder>")
        return "\n".join(lines)
