"""Tests for AuditLogger — Phase G1.

4 test cases:
  - 1 000 writes completes without error
  - DB file size stays < 1 MB after 1 000 writes
  - idx_audit_ts index exists
  - decision values validated (allowed / denied / error)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nerdvana_cli.server.audit import AuditLogger


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger(tmp_path) -> AuditLogger:
    db = tmp_path / "audit.sqlite"
    al = AuditLogger(db_path=db)
    al.open()
    yield al
    al.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_1000_writes_no_error(logger):
    """Writing 1 000 rows must not raise any exception."""
    for i in range(1000):
        logger.record(
            client_identity = "test-client",
            transport       = "stdio",
            tool_name       = "ReadMemory",
            args            = {"name": f"mem-{i}"},
            decision        = "allowed",
            duration_ms     = i,
        )
    assert logger.count() >= 1  # pruning may remove some rows


def test_db_size_under_1mb_after_1000_writes(logger):
    """After 1 000 rows the database file must stay under 1 MB (v3.1 §1.4)."""
    for i in range(1000):
        logger.record(
            client_identity = "bulk",
            transport       = "http",
            tool_name       = "find_symbol",
            args            = {"name_path": f"module.fn_{i}"},
            decision        = "allowed",
        )
    size = logger.db_size_bytes()
    assert size < 1_000_000, f"DB size {size} bytes exceeds 1 MB"


def test_idx_audit_ts_exists(logger):
    """The idx_audit_ts index must exist in the database schema."""
    db_path = logger._db_path
    conn    = sqlite3.connect(str(db_path))
    cur     = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    indices = {row[0] for row in cur.fetchall()}
    conn.close()
    assert "idx_audit_ts" in indices


def test_decision_values_stored_correctly(logger):
    """allowed / denied / error decisions must round-trip through SQLite."""
    logger.record(tool_name="ReadMemory", args={}, decision="allowed",
                  client_identity="a", transport="stdio")
    logger.record(tool_name="WriteMemory", args={}, decision="denied",
                  client_identity="b", transport="http")
    logger.record(tool_name="DeleteMemory", args={}, decision="error",
                  client_identity="c", transport="stdio", error_class="RuntimeError")

    rows = logger.recent(10)
    decisions = {r["decision"] for r in rows}
    assert {"allowed", "denied", "error"} == decisions

    error_rows = [r for r in rows if r["decision"] == "error"]
    assert error_rows[0]["error_class"] == "RuntimeError"
