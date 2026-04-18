"""Dual-gate sanitizer for hook-injected context — Phase G2 (v3.1 §3.3).

Gate 1 — Blacklist tag: known prompt-injection patterns are wrapped with
    ``<!-- SANITIZED:<pattern_id> -->…<!-- /SANITIZED -->`` (warn, not block).

Gate 2 — Structure reject: payloads that contain system-prompt injection
    structure (JSON ``"role": "system"`` / XML ``<system>…</system>`` / tool
    definition ``"function": {"name": …}``) are rejected outright; the caller
    receives an empty ``additionalContext`` and a stderr warning.

Sensitive redaction (all paths):
  - OpenAI-style API keys  → ``[REDACTED:OPENAI]``
  - AWS access-key IDs     → ``[REDACTED:AWS]``
  - E-mail addresses       → ``[REDACTED:EMAIL]``
  - Bare high-entropy tokens ≥ 16 chars → ``[REDACTED:TOKEN]``

Length cap: 4 096 characters; excess is truncated and ``[TRUNCATED]`` appended.

All events (gate1 warnings, gate2 rejections, redactions, truncations) are
recorded in the ``sanitizer_events`` table in ``audit.sqlite``.

30-day false-positive rate target: < 5 % (v3.1 §3.3).
Actual measurement requires production data — this module provides the
framework; events are stored in ``sanitizer_events`` for offline analysis.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import re
import sqlite3
import sys
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_LENGTH = 4_096

# Gate-1 blacklist: (pattern_id, compiled_regex)
_BLACKLIST: list[tuple[str, re.Pattern[str]]] = [
    (
        "ignore_previous",
        re.compile(r"(?i)ignore\s+(all\s+)?previous\s+(instructions|prompts)"),
    ),
    (
        "disregard_instructions",
        re.compile(r"(?i)disregard\s+.{0,30}\s+instructions"),
    ),
    (
        "you_are_now",
        re.compile(r"(?i)you\s+are\s+now\s+"),
    ),
    (
        "system_prompt_label",
        re.compile(r"(?i)system\s+prompt\s*:"),
    ),
    (
        "special_token",
        re.compile(r"<\|[^|]+\|>"),
    ),
    (
        "llama_sys_tag",
        re.compile(r"<<SYS>>"),
    ),
    (
        "inst_tag",
        re.compile(r"\[INST\]"),
    ),
]

# Gate-2 structure patterns (reject if any match)
_STRUCT_ROLE_SYSTEM   = re.compile(r'"role"\s*:\s*"system"')
_STRUCT_XML_SYSTEM    = re.compile(r"<system\b[^>]*>.*?</system\s*>", re.DOTALL | re.IGNORECASE)
_STRUCT_TOOL_DEF      = re.compile(r'"function"\s*:\s*\{[^}]{0,200}"name"\s*:')

# Sensitive-data redaction patterns: (replacement, compiled_regex)
_REDACT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("[REDACTED:OPENAI]", re.compile(r"sk-[A-Za-z0-9]{32,}")),
    ("[REDACTED:AWS]",    re.compile(r"AKIA[0-9A-Z]{16}")),
    ("[REDACTED:EMAIL]",  re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")),
    # High-entropy token: 16+ word chars not already matched above
    # Must come LAST so previous patterns are applied first
    ("[REDACTED:TOKEN]",  re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9+/]{16,}(?:[A-Za-z0-9+/=]{0,32})(?![A-Za-z0-9])")),
]


# ---------------------------------------------------------------------------
# Sanitizer result
# ---------------------------------------------------------------------------

@dataclass
class SanitizeResult:
    """Outcome of a :func:`sanitize` call.

    Attributes
    ----------
    text:
        Final text after all transformations (empty string when rejected).
    rejected:
        True if gate-2 structure detection triggered rejection.
    warnings:
        Number of gate-1 blacklist matches found.
    redactions:
        Number of sensitive-data redactions applied.
    truncated:
        True if the input was truncated to MAX_LENGTH.
    """

    text:       str
    rejected:   bool           = False
    warnings:   int            = 0
    redactions: int            = 0
    truncated:  bool           = False
    matched_patterns: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core sanitize function
# ---------------------------------------------------------------------------

def sanitize(text: str) -> SanitizeResult:
    """Apply dual-gate sanitisation to *text*.

    Returns a :class:`SanitizeResult` whose ``text`` field contains the
    transformed string (or ``""`` on rejection).  Callers should check
    ``rejected`` before using ``text``.
    """
    warnings       = 0
    redactions     = 0
    truncated      = False
    matched        : list[str] = []

    # --- Gate 2: structure detection (before any mutation so patterns match) ---
    if (
        _STRUCT_ROLE_SYSTEM.search(text)
        or _STRUCT_XML_SYSTEM.search(text)
        or _STRUCT_TOOL_DEF.search(text)
    ):
        _warn_stderr("[sanitizer] REJECTED: system-prompt injection structure detected")
        return SanitizeResult(text="", rejected=True)

    # --- Sensitive-data redaction ---
    for replacement, pattern in _REDACT_PATTERNS:
        new_text, count = pattern.subn(replacement, text)
        if count:
            redactions += count
            text        = new_text

    # --- Gate 1: blacklist tagging ---
    for pattern_id, pattern in _BLACKLIST:
        def _tag(m: re.Match[str], pid: str = pattern_id) -> str:
            return f"<!-- SANITIZED:{pid} -->{m.group(0)}<!-- /SANITIZED -->"

        new_text, count = pattern.subn(_tag, text)
        if count:
            warnings += count
            matched.append(pattern_id)
            text      = new_text

    # --- Length cap ---
    if len(text) > MAX_LENGTH:
        text      = text[:MAX_LENGTH] + "[TRUNCATED]"
        truncated = True

    return SanitizeResult(
        text            = text,
        rejected        = False,
        warnings        = warnings,
        redactions      = redactions,
        truncated       = truncated,
        matched_patterns= matched,
    )


# ---------------------------------------------------------------------------
# Stderr helper
# ---------------------------------------------------------------------------

def _warn_stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# SanitizerAudit — records events to sanitizer_events table
# ---------------------------------------------------------------------------

_DDL_SANITIZER_EVENTS = """
CREATE TABLE IF NOT EXISTS sanitizer_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    hook_name   TEXT,
    event_type  TEXT    NOT NULL,
    detail      TEXT,
    text_len    INTEGER
);
"""

_INSERT_EVENT = """
INSERT INTO sanitizer_events (ts, hook_name, event_type, detail, text_len)
VALUES (?, ?, ?, ?, ?)
"""


class SanitizerAudit:
    """Records sanitizer events to the shared audit SQLite database.

    Parameters
    ----------
    db_path:
        Path to ``audit.sqlite``.  Defaults to ``~/.nerdvana/audit.sqlite``.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path : Path                     = db_path or Path.home() / ".nerdvana" / "audit.sqlite"
        self._conn    : sqlite3.Connection | None = None
        self._lock    : threading.Lock            = threading.Lock()

    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open the database and ensure the ``sanitizer_events`` table exists."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.executescript(_DDL_SANITIZER_EVENTS)
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _ensure_open(self) -> None:
        if self._conn is None:
            self.open()

    # ------------------------------------------------------------------

    def record(
        self,
        *,
        hook_name:  str | None = None,
        event_type: str,
        detail:     str | None = None,
        text_len:   int | None = None,
    ) -> None:
        """Insert one sanitizer-event row."""
        self._ensure_open()
        assert self._conn is not None  # noqa: S101
        ts = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(_INSERT_EVENT, (ts, hook_name, event_type, detail, text_len))
            self._conn.commit()

    def record_result(self, result: SanitizeResult, *, hook_name: str | None = None, original_len: int) -> None:
        """Record all noteworthy events from a :class:`SanitizeResult`."""
        if result.rejected:
            self.record(hook_name=hook_name, event_type="gate2_reject", text_len=original_len)
        if result.warnings:
            self.record(
                hook_name  = hook_name,
                event_type = "gate1_warn",
                detail     = ",".join(result.matched_patterns),
                text_len   = original_len,
            )
        if result.redactions:
            self.record(hook_name=hook_name, event_type="redaction", text_len=original_len)
        if result.truncated:
            self.record(hook_name=hook_name, event_type="truncation", text_len=original_len)

    def count_by_type(self) -> dict[str, int]:
        """Return ``{event_type: count}`` summary (used in tests)."""
        self._ensure_open()
        assert self._conn is not None  # noqa: S101
        with self._lock:
            cur = self._conn.execute(
                "SELECT event_type, COUNT(*) FROM sanitizer_events GROUP BY event_type"
            )
            return {row[0]: row[1] for row in cur.fetchall()}

    def recent(self, n: int = 50) -> list[dict[str, Any]]:
        """Return the *n* most-recent event rows as dicts."""
        self._ensure_open()
        assert self._conn is not None  # noqa: S101
        with self._lock:
            cur  = self._conn.execute(
                "SELECT id, ts, hook_name, event_type, detail, text_len "
                "FROM sanitizer_events ORDER BY id DESC LIMIT ?",
                (n,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
