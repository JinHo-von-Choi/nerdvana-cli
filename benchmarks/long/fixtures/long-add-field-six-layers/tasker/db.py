"""SQLite connection and the ordered migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def migration_files() -> list[Path]:
    """Migration scripts in the order they are applied."""
    return sorted(MIGRATIONS.glob("*.sql"))


def apply(conn: sqlite3.Connection, files: list[Path]) -> None:
    """Run the given scripts on a connection."""
    for file in files:
        conn.executescript(file.read_text(encoding="utf-8"))


def connect(path: str = ":memory:") -> sqlite3.Connection:
    """A connection with every migration applied."""
    conn = sqlite3.connect(path)
    apply(conn, migration_files())
    return conn
