"""SQLite audit logger for NerdVana MCP server — Phase G1.

Schema:
  CREATE TABLE audit (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      ts              TEXT    NOT NULL,
      client_identity TEXT,
      transport       TEXT,
      tool_name       TEXT    NOT NULL,
      args_hash       TEXT,
      decision        TEXT    NOT NULL,
      duration_ms     INTEGER,
      error_class     TEXT
  );
  CREATE INDEX idx_audit_ts ON audit(ts);

WAL mode, file permissions 0600.
Rows are pruned to keep the file under 1 MB after every 1 000 writes (v3.1 §1.4).

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

Decision = Literal["allowed", "denied", "error"]

# Maximum database file size before pruning is triggered (bytes).
_MAX_DB_BYTES    = 1_000_000   # 1 MB
_PRUNE_INTERVAL  = 1_000       # prune check every N writes


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    client_identity TEXT,
    transport       TEXT,
    tool_name       TEXT    NOT NULL,
    args_hash       TEXT,
    decision        TEXT    NOT NULL,
    duration_ms     INTEGER,
    error_class     TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
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

_INSERT = """
INSERT INTO audit
    (ts, client_identity, transport, tool_name, args_hash, decision, duration_ms, error_class)
VALUES
    (?,  ?,               ?,         ?,         ?,         ?,        ?,           ?)
"""


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------


class AuditLogger:
    """Thread-safe SQLite audit logger.

    Parameters
    ----------
    db_path:
        Path to ``audit.sqlite``.  Defaults to ``~/.nerdvana/audit.sqlite``.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path:    Path            = db_path or Path.home() / ".nerdvana" / "audit.sqlite"
        self._conn:       sqlite3.Connection | None = None
        self._lock:       threading.Lock  = threading.Lock()
        self._write_count: int            = 0

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open (or create) the database and apply schema + WAL mode."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.executescript(_DDL)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.commit()
        # Restrict file to owner-read-write only (0600)
        os.chmod(self._db_path, 0o600)

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _ensure_open(self) -> None:
        if self._conn is None:
            self.open()

    @contextmanager
    def _cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        self._ensure_open()
        assert self._conn is not None  # noqa: S101 — post-open invariant
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        *,
        client_identity: str | None = None,
        transport:       str | None = None,
        tool_name:       str,
        args:            dict[str, Any] | None = None,
        decision:        Decision,
        duration_ms:     int | None = None,
        error_class:     str | None = None,
    ) -> None:
        """Insert one audit row.

        Parameters
        ----------
        client_identity: identity string from AuthManager
        transport:       "stdio" or "http"
        tool_name:       name of the MCP tool being called
        args:            raw tool arguments (will be SHA-256-hashed)
        decision:        "allowed", "denied", or "error"
        duration_ms:     wall-clock milliseconds the tool took (optional)
        error_class:     ``type(exc).__name__`` when *decision* == "error"
        """
        ts        = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        args_hash = _hash_args(args) if args is not None else None

        with self._cursor() as cur:
            cur.execute(
                _INSERT,
                (ts, client_identity, transport, tool_name, args_hash, decision, duration_ms, error_class),
            )
            self._write_count += 1

        if self._write_count % _PRUNE_INTERVAL == 0:
            self._maybe_prune()

    # ------------------------------------------------------------------
    # Pruning
    # ------------------------------------------------------------------

    def _maybe_prune(self) -> None:
        """Delete oldest rows if the database file exceeds _MAX_DB_BYTES."""
        if not self._db_path.exists():
            return
        size = self._db_path.stat().st_size
        if size <= _MAX_DB_BYTES:
            return
        self._prune()

    def _prune(self) -> None:
        """Delete the oldest 25 % of rows to reclaim space."""
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM audit")
            row = cur.fetchone()
            total = row[0] if row else 0
            if total == 0:
                return
            delete_count = max(1, total // 4)
            cur.execute(
                "DELETE FROM audit WHERE id IN "
                "(SELECT id FROM audit ORDER BY id ASC LIMIT ?)",
                (delete_count,),
            )

        # Reclaim disk space after deletion
        with self._cursor() as cur:
            cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")

    # ------------------------------------------------------------------
    # Query helpers (for tests / admin)
    # ------------------------------------------------------------------

    def recent(self, n: int = 50) -> list[dict[str, Any]]:
        """Return the *n* most recent audit rows as dicts."""
        self._ensure_open()
        assert self._conn is not None  # noqa: S101
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, ts, client_identity, transport, tool_name, "
                "args_hash, decision, duration_ms, error_class "
                "FROM audit ORDER BY id DESC LIMIT ?",
                (n,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

    def count(self) -> int:
        """Return total row count."""
        self._ensure_open()
        assert self._conn is not None  # noqa: S101
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM audit")
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def db_size_bytes(self) -> int:
        """Return current database file size in bytes."""
        if not self._db_path.exists():
            return 0
        return self._db_path.stat().st_size


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_args(args: dict[str, Any]) -> str:
    """Return SHA-256 hex digest of the canonical JSON serialisation of *args*."""
    canonical = json.dumps(args, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
