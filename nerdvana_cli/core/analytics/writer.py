"""Thread-safe analytics writer: sessions, provider requests, tool calls and approvals.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from nerdvana_cli.core.analytics.db import ATTRIBUTION_COLUMNS, DDL, connect, default_db_path
from nerdvana_cli.core.analytics.pricing import PricingTable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallOrigin:
    """Who made a provider request: the agent, its category and where in the run it stood."""

    agent_id:          str = "main"
    agent_type:        str = "main"
    category:          str = ""
    parent_session_id: str = ""
    turn:              int = 0
    last_tool:         str = ""


class AnalyticsWriter:
    """Thread-safe writer for analytics data.

    Instantiate once per session. Call ``start_session`` at the beginning,
    ``record_tool_call`` after each tool execution, and ``end_session`` on exit.

    Can be disabled via ``enabled=False`` for tests or offline scenarios.
    """

    def __init__(
        self,
        db_path:       Path | None    = None,
        pricing_table: PricingTable | None = None,
        enabled:       bool           = True,
    ) -> None:
        self._db_path      = db_path or default_db_path()
        self._pricing      = pricing_table or PricingTable()
        self._enabled      = enabled
        self._session_id:  str = ""
        self._lock         = threading.Lock()

        if self._enabled:
            self._ensure_schema()

    # ------------------------------------------------------------------
    # Schema setup
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        try:
            with connect(self._db_path) as conn:
                conn.executescript(DDL)
                # Databases created before cache accounting lack these columns.
                existing = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
                for column in ("cache_read_tokens", "cache_write_tokens"):
                    if column not in existing:
                        conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} INTEGER DEFAULT 0")
                # Request rows written before cost attribution lack these.
                existing = {row["name"] for row in conn.execute("PRAGMA table_info(api_calls)")}
                for column, kind in ATTRIBUTION_COLUMNS:
                    if column not in existing:
                        conn.execute(f"ALTER TABLE api_calls ADD COLUMN {column} {kind}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("analytics: failed to initialise schema: %s", exc)
            self._enabled = False

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start_session(
        self,
        session_id: str,
        mode:       str | None = None,
        context:    str | None = None,
    ) -> None:
        """Record session start."""
        self._session_id = session_id
        if not self._enabled:
            return
        ts = datetime.now(UTC).isoformat()
        try:
            with connect(self._db_path) as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO sessions
                       (id, started_at, mode, context)
                       VALUES (?, ?, ?, ?)""",
                    (session_id, ts, mode, context),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("analytics: start_session error: %s", exc)

    def end_session(
        self,
        token_total:        int   = 0,
        cost_total:         float = 0.0,
        cache_read_tokens:  int   = 0,
        cache_write_tokens: int   = 0,
    ) -> None:
        """Record session end and update totals."""
        if not self._enabled or not self._session_id:
            return
        ts = datetime.now(UTC).isoformat()
        try:
            with connect(self._db_path) as conn:
                conn.execute(
                    """UPDATE sessions
                       SET ended_at=?, token_total=?, cost_total=?,
                           cache_read_tokens=?, cache_write_tokens=?
                       WHERE id=?""",
                    (ts, token_total, cost_total, cache_read_tokens, cache_write_tokens, self._session_id),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("analytics: end_session error: %s", exc)

    def record_api_call(self, provider: str, model: str, usage: dict[str, int], origin: CallOrigin | None = None) -> float:
        """Persist the usage the provider reported for one request, cached tokens included.

        *origin* says which agent made it. ``last_tool`` is the tool that ran just before the request,
        so the request that digests a tool's output is counted under that tool. It is an order in time,
        not proof the tool caused the cost. Returns the estimated cost of the request in USD.
        """
        origin = origin or CallOrigin()
        read, write = usage.get("cache_read_tokens", 0), usage.get("cache_write_tokens", 0)
        inputs, outputs = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        cost = self._pricing.estimate_cost(provider, model, inputs, outputs, read, write)
        if not self._enabled:
            return cost
        with self._lock:
            try:
                with connect(self._db_path) as conn:
                    conn.execute(
                        """INSERT INTO api_calls
                           (session_id, ts, provider, model, input_tokens, output_tokens,
                            cache_read_tokens, cache_write_tokens, cost_usd,
                            agent_id, agent_type, category, parent_session_id, turn, last_tool)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            self._session_id, datetime.now(UTC).isoformat(), provider, model, inputs, outputs, read, write, cost,
                            origin.agent_id, origin.agent_type, origin.category, origin.parent_session_id, origin.turn, origin.last_tool,
                        ),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("analytics: record_api_call error: %s", exc)
        return cost

    def record_approval(self, tool_name: str, arg_key: str, granted: bool) -> None:
        """Persist the user's answer to one permission question."""
        if not self._enabled:
            return
        with self._lock:
            try:
                with connect(self._db_path) as conn:
                    conn.execute(
                        "INSERT INTO approvals (session_id, ts, tool_name, arg_key, decision) VALUES (?, ?, ?, ?, ?)",
                        (self._session_id, datetime.now(UTC).isoformat(), tool_name, arg_key, "allow" if granted else "deny"),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("analytics: record_approval error: %s", exc)

    # ------------------------------------------------------------------
    # Tool call recording
    # ------------------------------------------------------------------

    def record_tool_call(
        self,
        tool_name:     str,
        start_ts:      str,
        duration_ms:   int,
        success:       bool,
        error_class:   str | None = None,
        provider:      str | None = None,
        model:         str | None = None,
        input_tokens:  int        = 0,
        output_tokens: int        = 0,
    ) -> None:
        """Persist a single tool call record."""
        if not self._enabled:
            return

        cost = 0.0
        if provider and model:
            cost = self._pricing.estimate_cost(provider, model, input_tokens, output_tokens)

        with self._lock:
            try:
                with connect(self._db_path) as conn:
                    conn.execute(
                        """INSERT INTO tool_calls
                           (session_id, tool_name, start_ts, duration_ms,
                            success, error_class, provider, model,
                            input_tokens, output_tokens, cost_usd)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            self._session_id,
                            tool_name,
                            start_ts,
                            duration_ms,
                            1 if success else 0,
                            error_class,
                            provider,
                            model,
                            input_tokens,
                            output_tokens,
                            cost,
                        ),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("analytics: record_tool_call error: %s", exc)
