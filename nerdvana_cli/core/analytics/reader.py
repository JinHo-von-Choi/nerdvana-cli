"""Read-only analytics queries for /health, /cost and the dashboard.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nerdvana_cli.core.analytics.db import connect, default_db_path

logger = logging.getLogger(__name__)


class AnalyticsReader:
    """Read-only queries over analytics.sqlite for /health and dashboard."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or default_db_path()

    def _exists(self) -> bool:
        return self._db_path.exists()

    def summary(self, days: int = 7) -> dict[str, Any]:
        """Return aggregated stats for the last *days* days."""
        if not self._exists():
            return {"total_calls": 0, "total_tokens": 0, "total_cost_usd": 0.0, "top_failures": []}

        since = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        since -= timedelta(days=days - 1)
        since_str = since.isoformat()

        try:
            with connect(self._db_path) as conn:
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
            with connect(self._db_path) as conn:
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

    def cost_breakdown(self, session_id: str) -> dict[str, dict[str, float]]:
        """Requests and cost of a session and of the sub-agents it started, per agent type."""
        if not self._exists():
            return {}
        try:
            with connect(self._db_path) as conn:
                rows = conn.execute(
                    """SELECT COALESCE(NULLIF(agent_type, ''), 'main') AS agent, COUNT(*) AS requests, SUM(cost_usd) AS cost
                       FROM api_calls WHERE session_id = ? OR parent_session_id = ? GROUP BY agent""",
                    (session_id, session_id),
                ).fetchall()
            return {r["agent"]: {"requests": int(r["requests"]), "cost_usd": round(float(r["cost"] or 0.0), 6)} for r in rows}
        except Exception as exc:  # noqa: BLE001
            logger.debug("analytics: cost_breakdown error: %s", exc)
            return {}

    def approvals(self, days: int = 30) -> list[dict[str, Any]]:
        """Answers to permission questions of the last *days* days, counted per tool and argument."""
        if not self._exists():
            return []
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        try:
            with connect(self._db_path) as conn:
                rows = conn.execute(
                    """SELECT tool_name, arg_key,
                              SUM(decision = 'allow') AS allowed, SUM(decision = 'deny') AS denied
                       FROM approvals WHERE ts >= ? GROUP BY tool_name, arg_key""",
                    (cutoff,),
                ).fetchall()
            return [{"tool": r["tool_name"], "argument": r["arg_key"], "allowed": int(r["allowed"] or 0), "denied": int(r["denied"] or 0)} for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.debug("analytics: approvals error: %s", exc)
            return []

    def session_cost(self, session_id: str) -> float:
        """Return cumulative cost USD for a session."""
        if not self._exists():
            return 0.0
        try:
            with connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT SUM(cost_usd) AS total, COUNT(*) AS n FROM api_calls WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                if not row["n"]:
                    row = conn.execute(
                        "SELECT SUM(cost_usd) AS total FROM tool_calls WHERE session_id=?",
                        (session_id,),
                    ).fetchone()
            return float(row["total"] or 0.0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("analytics: session_cost error: %s", exc)
            return 0.0
