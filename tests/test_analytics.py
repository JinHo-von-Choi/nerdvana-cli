"""Tests for nerdvana_cli.core.analytics — SQLite schema, writes, and queries."""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test_analytics.sqlite"


@pytest.fixture
def writer(tmp_db: Path):
    from nerdvana_cli.core.analytics import AnalyticsWriter
    return AnalyticsWriter(db_path=tmp_db, enabled=True)


@pytest.fixture
def reader(tmp_db: Path, writer):  # writer ensures schema exists
    from nerdvana_cli.core.analytics import AnalyticsReader
    return AnalyticsReader(db_path=tmp_db)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_tables_created(self, writer, tmp_db: Path) -> None:
        conn = sqlite3.connect(str(tmp_db))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "tool_calls" in tables
        assert "sessions"   in tables

    def test_wal_mode(self, tmp_db: Path, writer) -> None:
        conn = sqlite3.connect(str(tmp_db))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"

    def test_tool_calls_columns(self, tmp_db: Path, writer) -> None:
        conn = sqlite3.connect(str(tmp_db))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tool_calls)").fetchall()}
        conn.close()
        expected = {
            "id", "session_id", "tool_name", "start_ts", "duration_ms",
            "success", "error_class", "provider", "model",
            "input_tokens", "output_tokens", "cost_usd",
        }
        assert expected <= cols

    def test_sessions_columns(self, tmp_db: Path, writer) -> None:
        conn = sqlite3.connect(str(tmp_db))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        conn.close()
        expected = {"id", "started_at", "ended_at", "mode", "context", "token_total", "cost_total"}
        assert expected <= cols

    def test_indexes_created(self, tmp_db: Path, writer) -> None:
        conn = sqlite3.connect(str(tmp_db))
        idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        conn.close()
        assert "idx_tool_calls_session" in idx
        assert "idx_tool_calls_ts"      in idx


# ---------------------------------------------------------------------------
# Write / read
# ---------------------------------------------------------------------------

class TestAnalyticsWriter:
    def test_start_session(self, writer, tmp_db: Path) -> None:
        writer.start_session("sess-001", mode="plan", context="default")
        conn = sqlite3.connect(str(tmp_db))
        row  = conn.execute("SELECT * FROM sessions WHERE id='sess-001'").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "sess-001"

    def test_end_session(self, writer, tmp_db: Path) -> None:
        writer.start_session("sess-002")
        writer.end_session(token_total=1000, cost_total=0.01)
        conn = sqlite3.connect(str(tmp_db))
        row  = conn.execute("SELECT token_total, cost_total, ended_at FROM sessions WHERE id='sess-002'").fetchone()
        conn.close()
        assert row[0] == 1000
        assert abs(row[1] - 0.01) < 1e-6
        assert row[2] is not None

    def test_record_tool_call_success(self, writer, tmp_db: Path) -> None:
        writer.start_session("sess-003")
        ts = datetime.now(timezone.utc).isoformat()
        writer.record_tool_call(
            tool_name    = "Bash",
            start_ts     = ts,
            duration_ms  = 120,
            success      = True,
            provider     = "anthropic",
            model        = "claude-sonnet-4-6",
            input_tokens = 100,
            output_tokens= 50,
        )
        conn = sqlite3.connect(str(tmp_db))
        row  = conn.execute("SELECT * FROM tool_calls WHERE session_id='sess-003'").fetchone()
        conn.close()
        assert row is not None
        assert row[2] == "Bash"  # tool_name
        assert row[5] == 1       # success

    def test_record_tool_call_failure(self, writer, tmp_db: Path) -> None:
        writer.start_session("sess-004")
        ts = datetime.now(timezone.utc).isoformat()
        writer.record_tool_call(
            tool_name   = "FileWrite",
            start_ts    = ts,
            duration_ms = 50,
            success     = False,
            error_class = "PermissionError",
        )
        conn = sqlite3.connect(str(tmp_db))
        row  = conn.execute("SELECT success, error_class FROM tool_calls WHERE session_id='sess-004'").fetchone()
        conn.close()
        assert row[0] == 0
        assert row[1] == "PermissionError"

    def test_cost_computed_on_write(self, writer, tmp_db: Path) -> None:
        writer.start_session("sess-005")
        ts = datetime.now(timezone.utc).isoformat()
        # claude-sonnet-4-6: input $3/1k, output $15/1k
        writer.record_tool_call(
            tool_name    = "Ask",
            start_ts     = ts,
            duration_ms  = 200,
            success      = True,
            provider     = "anthropic",
            model        = "claude-sonnet-4-6",
            input_tokens = 1000,
            output_tokens= 500,
        )
        conn = sqlite3.connect(str(tmp_db))
        row  = conn.execute("SELECT cost_usd FROM tool_calls WHERE session_id='sess-005'").fetchone()
        conn.close()
        # 1000*3/1000 + 500*15/1000 = 3 + 7.5 = 10.5
        assert abs(row[0] - 10.5) < 0.001

    def test_disabled_writer_no_writes(self, tmp_db: Path) -> None:
        from nerdvana_cli.core.analytics import AnalyticsWriter
        writer = AnalyticsWriter(db_path=tmp_db, enabled=False)
        writer.start_session("sess-disabled")
        # File should not exist since disabled before schema creation
        assert not tmp_db.exists()


# ---------------------------------------------------------------------------
# Reader / summary
# ---------------------------------------------------------------------------

class TestAnalyticsReader:
    def test_summary_no_db(self, tmp_path: Path) -> None:
        from nerdvana_cli.core.analytics import AnalyticsReader
        reader = AnalyticsReader(db_path=tmp_path / "nonexistent.sqlite")
        s = reader.summary()
        assert s["total_calls"] == 0
        assert s["total_cost_usd"] == 0.0

    def test_summary_with_data(self, writer, reader, tmp_db: Path) -> None:
        writer.start_session("sess-r1")
        ts = datetime.now(timezone.utc).isoformat()
        for i in range(3):
            writer.record_tool_call(
                tool_name="Bash", start_ts=ts, duration_ms=100, success=True
            )
        s = reader.summary(days=1)
        assert s["total_calls"] >= 3

    def test_session_cost_query(self, writer, reader) -> None:
        writer.start_session("sess-cost")
        ts = datetime.now(timezone.utc).isoformat()
        writer.record_tool_call(
            tool_name    = "Ask",
            start_ts     = ts,
            duration_ms  = 100,
            success      = True,
            provider     = "anthropic",
            model        = "claude-sonnet-4-6",
            input_tokens = 1000,
            output_tokens= 0,
        )
        cost = reader.session_cost("sess-cost")
        # 1000 input tokens * $3/1k = $3
        assert abs(cost - 3.0) < 0.001

    def test_recent_tool_buckets(self, writer, reader) -> None:
        writer.start_session("sess-buckets")
        ts = datetime.now(timezone.utc).isoformat()
        writer.record_tool_call(tool_name="Bash",    start_ts=ts, duration_ms=50, success=True)
        writer.record_tool_call(tool_name="Bash",    start_ts=ts, duration_ms=60, success=True)
        writer.record_tool_call(tool_name="FileRead", start_ts=ts, duration_ms=30, success=True)
        buckets = reader.recent_tool_buckets()
        names = [b["tool"] for b in buckets]
        assert "Bash" in names
