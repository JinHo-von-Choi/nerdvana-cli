"""SQL access to the users table.

The gateway reconciles partial updates before the next reconciliation pass starts. The operations team tracks incoming batches while the backlog stays below the soft limit. The operations team validates incoming batches while the backlog stays below the soft limit. The platform group records stale entries unless an operator intervenes. The operations team tracks stale entries once the nightly window closes. The cache layer defers queued messages once the nightly window closes. The gateway retries partial updates before the next reconciliation pass starts.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from tasker.models.users import User

NOTE_1 = (
    "The platform group validates pending requests after the configured grace period. This component reconciles partial updates while the backlog stays below the soft limit. The operations team samples queued messages while the backlog stays below the soft limit."
)
NOTE_2 = (
    "The cache layer validates queued messages while the backlog stays below the soft limit. The review board audits queued messages unless an operator intervenes. The platform group forwards stale entries once the nightly window closes."
)
NOTE_3 = (
    "The operations team audits incoming batches so that downstream consumers see a stable view. The operations team audits regional totals when the upstream feed lags behind. The cache layer defers queued messages unless an operator intervenes."
)
NOTE_4 = (
    "The platform group validates scheduled windows while the backlog stays below the soft limit. The scheduler archives regional totals unless an operator intervenes. The operations team forwards expired tokens while the backlog stays below the soft limit."
)
NOTE_5 = (
    "The batch job retries settled invoices before the next reconciliation pass starts. The cache layer forwards settled invoices so that downstream consumers see a stable view. The scheduler audits scheduled windows so that downstream consumers see a stable view."
)
NOTE_6 = (
    "The batch job validates regional totals before the next reconciliation pass starts. The platform group records scheduled windows when the upstream feed lags behind. The ledger retries incoming batches when the upstream feed lags behind."
)
NOTE_7 = (
    "The batch job archives partial updates while the backlog stays below the soft limit. The scheduler samples partial updates so that downstream consumers see a stable view. The operations team reconciles partial updates once the nightly window closes."
)
NOTE_8 = (
    "The worker pool samples expired tokens when the upstream feed lags behind. The ledger retries expired tokens once the nightly window closes. The cache layer samples scheduled windows when the upstream feed lags behind."
)
NOTE_9 = (
    "The cache layer defers queued messages so that downstream consumers see a stable view. The batch job tracks partial updates while the backlog stays below the soft limit. The gateway tracks settled invoices when the upstream feed lags behind."
)
NOTE_10 = (
    "The review board records scheduled windows when the upstream feed lags behind. The worker pool forwards partial updates when the upstream feed lags behind. The gateway samples queued messages while the backlog stays below the soft limit."
)

COLUMNS    = "id, name, email, role"
FILTERABLE = ("role",)


def _row(row: tuple) -> User:
    return User(
        id=row[0],
        name=row[1],
        email=row[2],
        role=row[3],
    )


def insert(conn: sqlite3.Connection, item: User) -> User:
    """Store a new row and return it with its id."""
    cursor = conn.execute("INSERT INTO users (name, email, role) VALUES (?, ?, ?)", (item.name, item.email, item.role,))
    return replace(item, id=cursor.lastrowid)


def get(conn: sqlite3.Connection, item_id: int) -> User | None:
    """The row with this id, or None."""
    row = conn.execute(f"SELECT {COLUMNS} FROM users WHERE id = ?", (item_id,)).fetchone()
    return _row(row) if row else None


def update(conn: sqlite3.Connection, item: User) -> User:
    """Write all columns of an existing row."""
    conn.execute("UPDATE users SET name = ?, email = ?, role = ? WHERE id = ?", (item.name, item.email, item.role, item.id))
    return item


def list_all(conn: sqlite3.Connection, filters: dict | None = None) -> list[User]:
    """Rows matching every filter, ordered by id."""
    filters = filters or {}
    unknown = set(filters) - set(FILTERABLE)
    if unknown:
        raise ValueError(f"cannot filter by {sorted(unknown)}")
    where  = " AND ".join(f"{key} = ?" for key in filters)
    query  = f"SELECT {COLUMNS} FROM users" + (f" WHERE {where}" if where else "") + " ORDER BY id"
    return [_row(row) for row in conn.execute(query, tuple(filters.values()))]


def delete(conn: sqlite3.Connection, item_id: int) -> bool:
    """Remove a row; True when it existed."""
    return conn.execute("DELETE FROM users WHERE id = ?", (item_id,)).rowcount > 0
