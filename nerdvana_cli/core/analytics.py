"""Analytics engine for NerdVana CLI — tool call stats + session cost tracking.

Storage: ``~/.nerdvana/analytics.sqlite`` (separate from audit.sqlite).

Schema:
    tool_calls — one row per tool invocation with timing, token, and cost data.
    sessions   — one row per CLI session with aggregated totals.

Both tables use WAL mode for concurrent read safety.

Classes:
    PricingTable   — loads pricing.yml, computes USD cost per call.
    AnalyticsWriter — thread-safe writer; records sessions + tool calls.
    AnalyticsReader — query helpers for /health and dashboard.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS tool_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    tool_name   TEXT NOT NULL,
    start_ts    TEXT NOT NULL,
    duration_ms INTEGER,
    success     INTEGER NOT NULL,
    error_class TEXT,
    provider    TEXT,
    model       TEXT,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd      REAL    DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_ts      ON tool_calls(start_ts);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT    PRIMARY KEY,
    started_at  TEXT    NOT NULL,
    ended_at    TEXT,
    mode        TEXT,
    context     TEXT,
    token_total INTEGER DEFAULT 0,
    cost_total  REAL    DEFAULT 0.0
);
"""


# ---------------------------------------------------------------------------
# PricingTable
# ---------------------------------------------------------------------------

class PricingTable:
    """Loads provider/model pricing from ``providers/pricing.yml``.

    Provides ``estimate_cost(provider, model, input_tokens, output_tokens)``.
    Falls back to 0.0 when a model is not in the table.
    """

    def __init__(self, pricing_path: Path | None = None) -> None:
        self._prices: dict[str, dict[str, dict[str, float]]] = {}
        path = pricing_path or self._default_path()
        self._load(path)

    @staticmethod
    def _default_path() -> Path:
        """Resolve the bundled pricing.yml next to this package."""
        # nerdvana_cli/providers/pricing.yml
        pkg_root = Path(__file__).parent.parent  # nerdvana_cli/
        return pkg_root / "providers" / "pricing.yml"

    def _load(self, path: Path) -> None:
        try:
            import yaml  # type: ignore[import-untyped]
            with open(path) as f:
                raw: dict[str, Any] = yaml.safe_load(f) or {}
            for provider, models in raw.items():
                if not isinstance(models, dict):
                    continue
                self._prices[provider.lower()] = {
                    m.lower(): {k: float(v) for k, v in info.items()}
                    for m, info in models.items()
                    if isinstance(info, dict)
                }
        except FileNotFoundError:
            logger.debug("pricing.yml not found at %s; all costs default to 0.0", path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to load pricing.yml: %s", exc)

    def estimate_cost(
        self,
        provider:      str,
        model:         str,
        input_tokens:  int,
        output_tokens: int,
    ) -> float:
        """Return estimated USD cost. Returns 0.0 for unknown models."""
        info = self._prices.get(provider.lower(), {}).get(model.lower(), {})
        if not info:
            return 0.0
        input_cost  = info.get("input_per_1k",  0.0) * input_tokens  / 1000.0
        output_cost = info.get("output_per_1k", 0.0) * output_tokens / 1000.0
        return input_cost + output_cost

    def known_providers(self) -> list[str]:
        return list(self._prices.keys())

    def known_models(self, provider: str) -> list[str]:
        return list(self._prices.get(provider.lower(), {}).keys())


# ---------------------------------------------------------------------------
# DB connection helper
# ---------------------------------------------------------------------------

@contextmanager
def _connect(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _default_db_path() -> Path:
    nerdvana_home = os.environ.get("NERDVANA_DATA_HOME", "").strip()
    base = Path(nerdvana_home).expanduser() if nerdvana_home else Path.home() / ".nerdvana"
    base.mkdir(parents=True, exist_ok=True)
    return base / "analytics.sqlite"


# ---------------------------------------------------------------------------
# AnalyticsWriter
# ---------------------------------------------------------------------------

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
        self._db_path      = db_path or _default_db_path()
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
            with _connect(self._db_path) as conn:
                conn.executescript(_DDL)
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
            with _connect(self._db_path) as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO sessions
                       (id, started_at, mode, context)
                       VALUES (?, ?, ?, ?)""",
                    (session_id, ts, mode, context),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("analytics: start_session error: %s", exc)

    def end_session(self, token_total: int = 0, cost_total: float = 0.0) -> None:
        """Record session end and update totals."""
        if not self._enabled or not self._session_id:
            return
        ts = datetime.now(UTC).isoformat()
        try:
            with _connect(self._db_path) as conn:
                conn.execute(
                    """UPDATE sessions
                       SET ended_at=?, token_total=?, cost_total=?
                       WHERE id=?""",
                    (ts, token_total, cost_total, self._session_id),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("analytics: end_session error: %s", exc)

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
                with _connect(self._db_path) as conn:
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


# ---------------------------------------------------------------------------
# AnalyticsReader
# ---------------------------------------------------------------------------

class AnalyticsReader:
    """Read-only queries over analytics.sqlite for /health and dashboard."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _default_db_path()

    def _exists(self) -> bool:
        return self._db_path.exists()

    def summary(self, days: int = 7) -> dict[str, Any]:
        """Return aggregated stats for the last *days* days."""
        if not self._exists():
            return {"total_calls": 0, "total_tokens": 0, "total_cost_usd": 0.0, "top_failures": []}

        since = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        from datetime import timedelta
        since -= timedelta(days=days - 1)
        since_str = since.isoformat()

        try:
            with _connect(self._db_path) as conn:
                row = conn.execute(
                    """SELECT COUNT(*) AS total_calls,
                              SUM(input_tokens + output_tokens) AS total_tokens,
                              SUM(cost_usd) AS total_cost
                       FROM tool_calls
                       WHERE start_ts >= ?""",
                    (since_str,),
                ).fetchone()

                failures = conn.execute(
                    """SELECT tool_name, COUNT(*) AS cnt
                       FROM tool_calls
                       WHERE start_ts >= ? AND success = 0
                       GROUP BY tool_name
                       ORDER BY cnt DESC
                       LIMIT 5""",
                    (since_str,),
                ).fetchall()

            return {
                "total_calls":    int(row["total_calls"] or 0),
                "total_tokens":   int(row["total_tokens"] or 0),
                "total_cost_usd": float(row["total_cost"] or 0.0),
                "top_failures":   [{"tool": r["tool_name"], "count": r["cnt"]} for r in failures],
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("analytics: summary query error: %s", exc)
            return {"total_calls": 0, "total_tokens": 0, "total_cost_usd": 0.0, "top_failures": []}

    def recent_tool_buckets(
        self,
        limit_tools: int = 10,
        bucket_minutes: int = 60,
    ) -> list[dict[str, Any]]:
        """Return tool call counts grouped by tool for sparkline rendering."""
        if not self._exists():
            return []
        try:
            with _connect(self._db_path) as conn:
                rows = conn.execute(
                    """SELECT tool_name, COUNT(*) AS cnt,
                              SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failures,
                              AVG(duration_ms) AS avg_ms
                       FROM tool_calls
                       GROUP BY tool_name
                       ORDER BY cnt DESC
                       LIMIT ?""",
                    (limit_tools,),
                ).fetchall()
            return [
                {
                    "tool":     r["tool_name"],
                    "count":    r["cnt"],
                    "failures": r["failures"],
                    "avg_ms":   round(r["avg_ms"] or 0),
                }
                for r in rows
            ]
        except Exception as exc:  # noqa: BLE001
            logger.debug("analytics: recent_tool_buckets error: %s", exc)
            return []

    def session_cost(self, session_id: str) -> float:
        """Return cumulative cost USD for a session."""
        if not self._exists():
            return 0.0
        try:
            with _connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT SUM(cost_usd) AS total FROM tool_calls WHERE session_id=?",
                    (session_id,),
                ).fetchone()
            return float(row["total"] or 0.0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("analytics: session_cost error: %s", exc)
            return 0.0
