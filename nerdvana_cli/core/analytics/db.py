"""SQLite plumbing of the analytics store: schema, connection helper and default location.

Storage: ``~/.nerdvana/analytics.sqlite`` (separate from audit.sqlite).

Schema:
    tool_calls: one row per tool invocation with timing, token, and cost data.
    api_calls:  one row per provider request with usage, cost and the agent that made it.
    approvals:  one row per answer to a permission question.
    sessions:   one row per CLI session with aggregated totals.

Both tables use WAL mode for concurrent read safety.

Author: 최진호
Date:   2026-10-03
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from nerdvana_cli.core import paths

DDL = """
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

CREATE TABLE IF NOT EXISTS api_calls (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         TEXT,
    ts                 TEXT    NOT NULL,
    provider           TEXT,
    model              TEXT,
    input_tokens       INTEGER DEFAULT 0,
    output_tokens      INTEGER DEFAULT 0,
    cache_read_tokens  INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    cost_usd           REAL    DEFAULT 0.0,
    agent_id           TEXT,
    agent_type         TEXT,
    category           TEXT,
    parent_session_id  TEXT,
    turn               INTEGER DEFAULT 0,
    last_tool          TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_calls_session ON api_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_api_calls_ts      ON api_calls(ts);

CREATE TABLE IF NOT EXISTS approvals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    ts          TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    arg_key     TEXT,
    decision    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_approvals_call ON approvals(tool_name, arg_key);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT    PRIMARY KEY,
    started_at  TEXT    NOT NULL,
    ended_at    TEXT,
    mode        TEXT,
    context     TEXT,
    token_total INTEGER DEFAULT 0,
    cost_total  REAL    DEFAULT 0.0,
    cache_read_tokens  INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0
);
"""


ATTRIBUTION_COLUMNS = (
    ("agent_id", "TEXT"), ("agent_type", "TEXT"), ("category", "TEXT"),
    ("parent_session_id", "TEXT"), ("turn", "INTEGER DEFAULT 0"), ("last_tool", "TEXT"),
)


@contextmanager
def connect(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
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


def default_db_path() -> Path:
    path = paths.analytics_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
