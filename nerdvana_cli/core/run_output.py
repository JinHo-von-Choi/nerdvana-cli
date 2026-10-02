"""Output formats and exit codes for the non-interactive ``nerdvana run``.

Author: 최진호
Date:   2026-10-03

Three formats exist. ``text`` is the human stream the REPL also shows. ``json``
prints one result object when the run ends. ``stream-json`` prints one JSON
object per line as the run progresses and ends with the same result object.
In both JSON formats standard output carries only JSON; diagnostics go to
standard error.

The shape of these objects is a public contract: fields are added, never
renamed, and ``schema_version`` changes only when one is removed or retyped.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rich.text import Text

from nerdvana_cli.core.agent_loop import (
    COMPACT_STATUS_PREFIX,
    CONTEXT_USAGE_PREFIX,
    TOOL_DONE_PREFIX,
    TOOL_STATUS_PREFIX,
)

SCHEMA_VERSION = 1
FORMATS        = ("text", "json", "stream-json")

EXIT_OK      = 0
EXIT_FAILURE = 1
EXIT_CONFIG  = 2
EXIT_BUDGET  = 3

# AgentLoop.last_stop -> (result subtype, is_error, exit code)
_OUTCOMES: dict[str, tuple[str, bool, int]] = {
    "completed":      ("success",            False, EXIT_OK),
    "max_turns":      ("error_max_turns",    True,  EXIT_BUDGET),
    "max_cost":       ("error_max_cost",     True,  EXIT_BUDGET),
    "max_tokens":     ("error_max_tokens",   True,  EXIT_FAILURE),
    "provider_error": ("error_provider",     True,  EXIT_FAILURE),
    "error":          ("error_during_run",   True,  EXIT_FAILURE),
    "config":         ("error_config",       True,  EXIT_CONFIG),
}

# A system notice from the loop starts with a rich style tag, e.g. "[bold red]...".
_NOTICE = re.compile(r"^\s*\[(?:(?:bold|dim|italic) )*(?:red|yellow|cyan|green|magenta|blue)\b")
_LINE_BREAKS = ("\u0085", "\u2028", "\u2029")
_TOOL_DONE = re.compile(r"^(?P<name>.*) \[(?P<status>done|error)\]$")


@dataclass
class RunResult:
    """What a finished run reports."""

    stop:        str                 = "completed"
    result:      str                 = ""
    session_id:  str                 = ""
    provider:    str                 = ""
    model:       str                 = ""
    turns:       int                 = 0
    duration_ms: int                 = 0
    cost_usd:    float               = 0.0
    usage:       dict[str, int]      = field(default_factory=dict)
    error:       str                 = ""

    @property
    def exit_code(self) -> int:
        """Process exit code for this outcome."""
        return _OUTCOMES.get(self.stop, _OUTCOMES["error"])[2]

    def to_dict(self) -> dict[str, Any]:
        """The ``result`` object of the JSON formats."""
        subtype, is_error, _ = _OUTCOMES.get(self.stop, _OUTCOMES["error"])
        payload: dict[str, Any] = {
            "type":           "result",
            "schema_version": SCHEMA_VERSION,
            "subtype":        subtype,
            "is_error":       is_error,
            "result":         self.result,
            "session_id":     self.session_id,
            "provider":       self.provider,
            "model":          self.model,
            "num_turns":      self.turns,
            "duration_ms":    self.duration_ms,
            "total_cost_usd": round(self.cost_usd, 6),
            "usage": {
                "input_tokens":       self.usage.get("input_tokens", 0),
                "output_tokens":      self.usage.get("output_tokens", 0),
                "cache_read_tokens":  self.usage.get("cache_read_tokens", 0),
                "cache_write_tokens": self.usage.get("cache_write_tokens", 0),
            },
        }
        if self.error:
            payload["error"] = self.error
        return payload


def _plain(markup: str) -> str:
    """The text of a rich-markup string; the raw string when it does not parse."""
    try:
        return Text.from_markup(markup).plain.strip()
    except Exception:  # noqa: BLE001
        return markup.strip()


def classify_chunk(chunk: str) -> dict[str, Any]:
    """Turn one string yielded by ``AgentLoop.run`` into a structured event."""
    if chunk.startswith(TOOL_STATUS_PREFIX):
        name, _, summary = chunk[len(TOOL_STATUS_PREFIX):].partition(" ")
        return {"type": "tool_start", "name": name, "summary": summary}
    if chunk.startswith(TOOL_DONE_PREFIX):
        body  = chunk[len(TOOL_DONE_PREFIX):]
        match = _TOOL_DONE.match(body)
        if match:
            return {"type": "tool_done", "name": match["name"], "is_error": match["status"] == "error"}
        return {"type": "tool_done", "name": body, "is_error": False}
    if chunk.startswith(CONTEXT_USAGE_PREFIX):
        try:
            percent = int(chunk[len(CONTEXT_USAGE_PREFIX):])
        except ValueError:
            percent = 0
        return {"type": "context", "percent": percent}
    if chunk.startswith(COMPACT_STATUS_PREFIX):
        return {"type": "compaction", "status": chunk[len(COMPACT_STATUS_PREFIX):]}
    if _NOTICE.match(chunk):
        return {"type": "notice", "text": _plain(chunk)}
    return {"type": "text", "text": chunk}


class RunReporter:
    """Writes a run's progress and outcome in the chosen format."""

    def __init__(
        self,
        fmt:     str,
        write:   Callable[[str], None],
        console: Any = None,
    ) -> None:
        if fmt not in FORMATS:
            raise ValueError(f"unknown output format {fmt!r}; expected one of {', '.join(FORMATS)}")
        self._fmt     = fmt
        self._write   = write
        self._console = console
        self._answer: list[str] = []

    @property
    def machine_readable(self) -> bool:
        """True for the JSON formats, where stdout must carry only JSON."""
        return self._fmt != "text"

    def _line(self, payload: dict[str, Any]) -> None:
        text = json.dumps(payload, ensure_ascii=False)
        # Some readers (Python's str.splitlines among them) also break lines at these
        # characters, which JSON allows unescaped; escape them so one line is one event.
        for char in _LINE_BREAKS:
            text = text.replace(char, f"\\u{ord(char):04x}")
        self._write(text + "\n")

    def start(self, session_id: str, provider: str, model: str) -> None:
        """Announce the run (stream-json only)."""
        if self._fmt == "stream-json":
            self._line({
                "type": "system", "subtype": "init", "schema_version": SCHEMA_VERSION,
                "session_id": session_id, "provider": provider, "model": model,
            })

    def chunk(self, chunk: str) -> None:
        """Report one string yielded by the agent loop."""
        if self._fmt == "text":
            self._console.print(chunk, end="")
            return
        event = classify_chunk(chunk)
        if event["type"] == "tool_start":
            self._answer.clear()  # text before a tool call is commentary, not the answer
        elif event["type"] == "text":
            self._answer.append(event["text"])
        if self._fmt == "stream-json":
            self._line(event)

    def final_text(self) -> str:
        """The answer: the text produced after the last tool call."""
        return "".join(self._answer).strip()

    def finish(self, outcome: RunResult) -> None:
        """Report how the run ended."""
        if self._fmt == "text":
            self._console.print()
            return
        if not outcome.result:
            outcome.result = self.final_text()
        self._line(outcome.to_dict())

    def failure(self, outcome: RunResult, message: str) -> None:
        """Report a run that could not start (configuration, credentials)."""
        outcome.error = message
        if self._fmt == "text":
            self._console.print(f"[red]Error: {message}[/red]")
            return
        self._line(outcome.to_dict())
