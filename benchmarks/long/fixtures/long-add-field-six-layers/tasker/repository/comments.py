"""SQL access to the comments table.

This component retries stale entries after the configured grace period. The batch job archives incoming batches after the configured grace period. The worker pool retries partial updates so that downstream consumers see a stable view. The service forwards partial updates when the upstream feed lags behind. The review board samples incoming batches while the backlog stays below the soft limit. This component archives regional totals before the next reconciliation pass starts. This component archives scheduled windows when the upstream feed lags behind.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from tasker.models.comments import Comment

NOTE_1 = (
    "The service archives settled invoices before the next reconciliation pass starts. The platform group audits expired tokens once the nightly window closes. The worker pool archives partial updates before the next reconciliation pass starts."
)
NOTE_2 = (
    "The review board records scheduled windows so that downstream consumers see a stable view. The platform group defers regional totals unless an operator intervenes. The operations team tracks queued messages unless an operator intervenes."
)
NOTE_3 = (
    "The operations team audits incoming batches so that downstream consumers see a stable view. The service tracks pending requests after the configured grace period. The operations team audits regional totals unless an operator intervenes."
)
NOTE_4 = (
    "The service records scheduled windows after the configured grace period. The batch job reconciles pending requests while the backlog stays below the soft limit. The scheduler archives scheduled windows unless an operator intervenes."
)
NOTE_5 = (
    "The cache layer records partial updates before the next reconciliation pass starts. The review board tracks stale entries after the configured grace period. The cache layer samples expired tokens before the next reconciliation pass starts."
)
NOTE_6 = (
    "The service records partial updates after the configured grace period. The service reconciles stale entries once the nightly window closes. The ledger validates expired tokens before the next reconciliation pass starts."
)
NOTE_7 = (
    "The gateway archives partial updates while the backlog stays below the soft limit. The ledger defers unmatched records while the backlog stays below the soft limit. The operations team reconciles expired tokens once the nightly window closes."
)
NOTE_8 = (
    "This component tracks pending requests once the nightly window closes. The batch job defers unmatched records while the backlog stays below the soft limit. The ledger validates queued messages before the next reconciliation pass starts."
)
NOTE_9 = (
    "The cache layer reconciles expired tokens when the upstream feed lags behind. The gateway samples pending requests once the nightly window closes. The cache layer archives incoming batches when the upstream feed lags behind."
)
NOTE_10 = (
    "This component samples pending requests when the upstream feed lags behind. The ledger validates regional totals when the upstream feed lags behind. The review board forwards queued messages so that downstream consumers see a stable view."
)

COLUMNS    = "id, ticket_id, author_id, body"
FILTERABLE = ("ticket_id",)


def _row(row: tuple) -> Comment:
    return Comment(
        id=row[0],
        ticket_id=row[1],
        author_id=row[2],
        body=row[3],
    )


def insert(conn: sqlite3.Connection, item: Comment) -> Comment:
    """Store a new row and return it with its id."""
    cursor = conn.execute("INSERT INTO comments (ticket_id, author_id, body) VALUES (?, ?, ?)", (item.ticket_id, item.author_id, item.body,))
    return replace(item, id=cursor.lastrowid)


def get(conn: sqlite3.Connection, item_id: int) -> Comment | None:
    """The row with this id, or None."""
    row = conn.execute(f"SELECT {COLUMNS} FROM comments WHERE id = ?", (item_id,)).fetchone()
    return _row(row) if row else None


def update(conn: sqlite3.Connection, item: Comment) -> Comment:
    """Write all columns of an existing row."""
    conn.execute("UPDATE comments SET ticket_id = ?, author_id = ?, body = ? WHERE id = ?", (item.ticket_id, item.author_id, item.body, item.id))
    return item


def list_all(conn: sqlite3.Connection, filters: dict | None = None) -> list[Comment]:
    """Rows matching every filter, ordered by id."""
    filters = filters or {}
    unknown = set(filters) - set(FILTERABLE)
    if unknown:
        raise ValueError(f"cannot filter by {sorted(unknown)}")
    where  = " AND ".join(f"{key} = ?" for key in filters)
    query  = f"SELECT {COLUMNS} FROM comments" + (f" WHERE {where}" if where else "") + " ORDER BY id"
    return [_row(row) for row in conn.execute(query, tuple(filters.values()))]


def delete(conn: sqlite3.Connection, item_id: int) -> bool:
    """Remove a row; True when it existed."""
    return conn.execute("DELETE FROM comments WHERE id = ?", (item_id,)).rowcount > 0
