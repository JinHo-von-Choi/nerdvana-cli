"""Hook bridge — Phase G2 full implementation.

Reads a JSON payload from stdin, routes to the appropriate hook handler,
applies sanitisation, records to audit DB, and writes a JSON response to
stdout.

Supported hook types (Claude Code / Codex / VSCode):
  - pre-tool-use      → permission decision + optional context injection
  - post-tool-use     → additional context injection
  - prompt-submit     → additional context injection (AnchorMind placeholder)

AnchorMind injection is opt-in via ``nerdvana.yml`` key
``hooks.anchormind_inject`` (default ``false``).  When disabled a placeholder
comment is returned instead of a real recall result.

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC
from pathlib import Path
from typing import Any

from nerdvana_cli.server.hook_schemas import (
    HOOK_NAMES,
    HookResponse,
    PermissionDecision,
    make_response,
)
from nerdvana_cli.server.sanitizer import SanitizerAudit, SanitizeResult, sanitize

# ---------------------------------------------------------------------------
# Audit DDL for hooks table
# ---------------------------------------------------------------------------

_DDL_HOOKS = """
CREATE TABLE IF NOT EXISTS hooks (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                      TEXT    NOT NULL,
    hook_name               TEXT    NOT NULL,
    tool_name               TEXT,
    permission_decision     TEXT,
    sanitizer_warnings      INTEGER DEFAULT 0,
    sanitizer_rejections    INTEGER DEFAULT 0,
    additional_context_len  INTEGER DEFAULT 0,
    duration_ms             INTEGER
);
"""


# ---------------------------------------------------------------------------
# HookBridge
# ---------------------------------------------------------------------------

class HookBridge:
    """Routes hook payloads to handlers, applies sanitiser, records audit rows.

    Parameters
    ----------
    db_path:
        Path to ``audit.sqlite``.  Defaults to ``~/.nerdvana/audit.sqlite``.
    anchormind_inject:
        When ``True``, the ``prompt-submit`` handler will attempt to inject
        AnchorMind recall context.  Currently always a placeholder (Phase G2).
    """

    def __init__(
        self,
        db_path:           Path | None = None,
        anchormind_inject: bool        = False,
    ) -> None:
        self._db_path          : Path           = db_path or Path.home() / ".nerdvana" / "audit.sqlite"
        self._anchormind_inject: bool           = anchormind_inject
        self._audit            : SanitizerAudit | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def dispatch(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Route *payload* to the matching hook handler.

        Returns a :class:`HookResponse`-compatible dict.

        Unknown hook names return a no-op response with
        ``permissionDecision="approve"``.
        """
        t0        = time.monotonic()
        hook_name = payload.get("hook_event_name", "")
        # Normalise to lower-kebab form (Claude uses "PreToolUse"; CLI uses "pre-tool-use")
        hook_key  = _normalise_hook_name(hook_name)

        if hook_key == "pre-tool-use":
            response = self._handle_pre_tool_use(payload)
        elif hook_key == "post-tool-use":
            response = self._handle_post_tool_use(payload)
        elif hook_key == "prompt-submit":
            response = self._handle_prompt_submit(payload)
        else:
            # Unknown hook — approve passthrough
            response = make_response(permission_decision="approve")

        duration_ms = int((time.monotonic() - t0) * 1000)
        self._record_hook(hook_key, payload, response, duration_ms)
        return dict(response)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_pre_tool_use(self, payload: dict[str, Any]) -> HookResponse:
        """Handle ``pre-tool-use``: approve by default, inject context."""
        tool_name = payload.get("tool_name", "")
        decision  : PermissionDecision = "approve"

        # AnchorMind injection (placeholder — opt-in, disabled by default)
        context = self._maybe_anchormind_context(f"pre-tool-use:{tool_name}")

        if context:
            result  = sanitize(context)
            context = "" if result.rejected else result.text
        else:
            result = SanitizeResult(text="")

        self._record_sanitize(result, hook_name="pre-tool-use", original_len=len(context))
        return make_response(permission_decision=decision, additional_context=context)

    def _handle_post_tool_use(self, payload: dict[str, Any]) -> HookResponse:
        """Handle ``post-tool-use``: inject context based on tool output."""
        tool_response = payload.get("tool_response", {})
        raw_context   = _extract_tool_output_summary(tool_response)

        if raw_context:
            result  = sanitize(raw_context)
            context = "" if result.rejected else result.text
        else:
            result  = SanitizeResult(text="")
            context = ""

        self._record_sanitize(result, hook_name="post-tool-use", original_len=len(raw_context))
        return make_response(additional_context=context)

    def _handle_prompt_submit(self, payload: dict[str, Any]) -> HookResponse:
        """Handle ``prompt-submit`` (UserPromptSubmit): sanitise user prompt.

        Sanitises the incoming prompt text and injects AnchorMind context
        (placeholder) when ``anchormind_inject`` is enabled.
        """
        prompt = payload.get("prompt", "")

        result = sanitize(prompt) if prompt else SanitizeResult(text="")

        self._record_sanitize(result, hook_name="prompt-submit", original_len=len(prompt))

        # AnchorMind recall injection (placeholder)
        injected = self._maybe_anchormind_context("prompt-submit")
        if injected:
            inj_result = sanitize(injected)
            injected   = "" if inj_result.rejected else inj_result.text
            self._record_sanitize(inj_result, hook_name="prompt-submit", original_len=len(injected))

        return make_response(additional_context=injected)

    # ------------------------------------------------------------------
    # AnchorMind placeholder
    # ------------------------------------------------------------------

    def _maybe_anchormind_context(self, topic: str) -> str:
        """Return AnchorMind recall context, or empty string.

        In Phase G2 this is always a placeholder when ``anchormind_inject``
        is True; real MCP recall is deferred to Phase G3+.
        """
        if not self._anchormind_inject:
            return ""
        # Placeholder — real implementation calls AnchorMind MCP
        return f"[AnchorMind placeholder — topic={topic}]"

    # ------------------------------------------------------------------
    # Audit helpers
    # ------------------------------------------------------------------

    def _get_audit(self) -> SanitizerAudit:
        if self._audit is None:
            self._audit = SanitizerAudit(self._db_path)
            self._audit.open()
            # Ensure hooks table exists in the same DB
            assert self._audit._conn is not None  # noqa: SLF001
            self._audit._conn.executescript(_DDL_HOOKS)
            self._audit._conn.commit()
        return self._audit

    def _record_sanitize(
        self,
        result:       SanitizeResult,
        *,
        hook_name:    str,
        original_len: int,
    ) -> None:
        import contextlib
        with contextlib.suppress(Exception):
            self._get_audit().record_result(result, hook_name=hook_name, original_len=original_len)

    def _record_hook(
        self,
        hook_name:   str,
        payload:     dict[str, Any],
        response:    HookResponse,
        duration_ms: int,
    ) -> None:
        try:
            audit = self._get_audit()
            assert audit._conn is not None  # noqa: SLF001
            from datetime import datetime
            ts          = datetime.now(UTC).isoformat()
            tool_name   = payload.get("tool_name")
            hso         = response.get("hookSpecificOutput", {})
            decision    = hso.get("permissionDecision")
            ctx_len     = len(hso.get("additionalContext", ""))
            with audit._lock:  # noqa: SLF001
                audit._conn.execute(  # noqa: SLF001
                    "INSERT INTO hooks "
                    "(ts, hook_name, tool_name, permission_decision, "
                    " sanitizer_warnings, sanitizer_rejections, additional_context_len, duration_ms) "
                    "VALUES (?, ?, ?, ?, 0, 0, ?, ?)",
                    (ts, hook_name, tool_name, decision, ctx_len, duration_ms),
                )
                audit._conn.commit()  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# stdin / stdout wrappers
# ---------------------------------------------------------------------------

def read_hook_payload(stream: Any = None) -> dict[str, Any]:
    """Read one JSON object from *stream* (defaults to ``sys.stdin``).

    Returns the parsed dict, or an empty dict on EOF / parse error.
    """
    src  = stream or sys.stdin
    line = src.readline()
    if not line:
        return {}
    try:
        parsed: Any = json.loads(line)
        return dict(parsed)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def write_hook_response(response: dict[str, Any], stream: Any = None) -> None:
    """Serialise *response* as compact JSON and write to *stream* (default stdout)."""
    dest = stream or sys.stdout
    dest.write(json.dumps(response, ensure_ascii=False))
    dest.write("\n")
    if hasattr(dest, "flush"):
        dest.flush()


def run_hook(hook_name: str, db_path: Path | None = None) -> int:
    """Read stdin → dispatch → write stdout.

    Returns exit code (0 on success, 1 on fatal error).
    """
    payload = read_hook_payload()
    # If caller passes hook name via CLI, inject into payload
    if hook_name and "hook_event_name" not in payload:
        payload["hook_event_name"] = _cli_name_to_event(hook_name)

    bridge   = HookBridge(db_path=db_path)
    response = bridge.dispatch(payload)
    write_hook_response(response)
    return 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise_hook_name(name: str) -> str:
    """Normalise hook event name to lower-kebab CLI form.

    ``"PreToolUse"`` → ``"pre-tool-use"``
    ``"UserPromptSubmit"`` → ``"prompt-submit"``
    """
    mapping = {
        "pretooluse":        "pre-tool-use",
        "posttooluse":       "post-tool-use",
        "userprompsubmit":   "prompt-submit",
        "userpromptsubmit":  "prompt-submit",
        "promptsubmit":      "prompt-submit",
    }
    normalised = name.lower().replace("-", "").replace("_", "")
    if normalised in mapping:
        return mapping[normalised]
    # Already kebab-form passthrough
    lower = name.lower()
    if lower in HOOK_NAMES:
        return lower
    return lower


def _cli_name_to_event(cli_name: str) -> str:
    """Convert CLI sub-command name to ``hook_event_name`` string."""
    mapping = {
        "pre-tool-use":   "PreToolUse",
        "post-tool-use":  "PostToolUse",
        "prompt-submit":  "UserPromptSubmit",
    }
    return mapping.get(cli_name, cli_name)


def _extract_tool_output_summary(tool_response: dict[str, Any]) -> str:
    """Extract a short summary string from a tool response dict."""
    if not tool_response:
        return ""
    # Common fields: "output", "content", "error"
    for key in ("output", "content", "result", "error"):
        val = tool_response.get(key)
        if isinstance(val, str) and val.strip():
            return val[:512]  # Cap at 512 before full sanitiser runs
    return ""
