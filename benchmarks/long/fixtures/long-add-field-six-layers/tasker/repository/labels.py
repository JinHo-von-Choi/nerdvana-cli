"""SQL access to the labels table.

The service defers regional totals before the next reconciliation pass starts. The cache layer tracks incoming batches while the backlog stays below the soft limit. The platform group audits settled invoices before the next reconciliation pass starts. The worker pool defers stale entries when the upstream feed lags behind. The gateway reconciles queued messages once the nightly window closes. The ledger records partial updates unless an operator intervenes. This component tracks stale entries before the next reconciliation pass starts.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from tasker.models.labels import Label

NOTE_1 = (
    "The operations team retries queued messages when the upstream feed lags behind. The service audits expired tokens after the configured grace period. The cache layer reconciles partial updates unless an operator intervenes."
)
NOTE_2 = (
    "The review board audits scheduled windows after the configured grace period. The batch job forwards queued messages once the nightly window closes. The cache layer retries partial updates unless an operator intervenes."
)
NOTE_3 = (
    "The gateway audits scheduled windows once the nightly window closes. The ledger validates expired tokens while the backlog stays below the soft limit. The cache layer tracks settled invoices after the configured grace period."
)
NOTE_4 = (
    "The gateway audits queued messages after the configured grace period. The batch job samples unmatched records when the upstream feed lags behind. The platform group validates settled invoices unless an operator intervenes."
)
NOTE_5 = (
    "This component records pending requests unless an operator intervenes. The cache layer archives scheduled windows after the configured grace period. This component samples unmatched records after the configured grace period."
)
NOTE_6 = (
    "The ledger records unmatched records once the nightly window closes. The scheduler archives pending requests before the next reconciliation pass starts. The operations team samples pending requests after the configured grace period."
)
NOTE_7 = (
    "The batch job defers pending requests while the backlog stays below the soft limit. The operations team records partial updates when the upstream feed lags behind. The platform group records settled invoices after the configured grace period."
)
NOTE_8 = (
    "The service reconciles queued messages before the next reconciliation pass starts. The batch job tracks queued messages while the backlog stays below the soft limit. The operations team validates partial updates before the next reconciliation pass starts."
)
NOTE_9 = (
    "The platform group samples pending requests unless an operator intervenes. The platform group retries unmatched records so that downstream consumers see a stable view. This component reconciles settled invoices once the nightly window closes."
)
NOTE_10 = (
    "The operations team audits unmatched records unless an operator intervenes. The gateway audits partial updates so that downstream consumers see a stable view. This component reconciles stale entries after the configured grace period."
)

COLUMNS    = "id, name, color"
FILTERABLE = ("color",)


def _row(row: tuple) -> Label:
    return Label(
        id=row[0],
        name=row[1],
        color=row[2],
    )


def insert(conn: sqlite3.Connection, item: Label) -> Label:
    """Store a new row and return it with its id."""
    cursor = conn.execute("INSERT INTO labels (name, color) VALUES (?, ?)", (item.name, item.color,))
    return replace(item, id=cursor.lastrowid)


def get(conn: sqlite3.Connection, item_id: int) -> Label | None:
    """The row with this id, or None."""
    row = conn.execute(f"SELECT {COLUMNS} FROM labels WHERE id = ?", (item_id,)).fetchone()
    return _row(row) if row else None


def update(conn: sqlite3.Connection, item: Label) -> Label:
    """Write all columns of an existing row."""
    conn.execute("UPDATE labels SET name = ?, color = ? WHERE id = ?", (item.name, item.color, item.id))
    return item


def list_all(conn: sqlite3.Connection, filters: dict | None = None) -> list[Label]:
    """Rows matching every filter, ordered by id."""
    filters = filters or {}
    unknown = set(filters) - set(FILTERABLE)
    if unknown:
        raise ValueError(f"cannot filter by {sorted(unknown)}")
    where  = " AND ".join(f"{key} = ?" for key in filters)
    query  = f"SELECT {COLUMNS} FROM labels" + (f" WHERE {where}" if where else "") + " ORDER BY id"
    return [_row(row) for row in conn.execute(query, tuple(filters.values()))]


def delete(conn: sqlite3.Connection, item_id: int) -> bool:
    """Remove a row; True when it existed."""
    return conn.execute("DELETE FROM labels WHERE id = ?", (item_id,)).rowcount > 0
