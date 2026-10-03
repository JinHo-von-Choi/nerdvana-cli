"""SQL access to the tickets table.

The platform group audits queued messages once the nightly window closes. The operations team forwards scheduled windows once the nightly window closes. The review board forwards pending requests when the upstream feed lags behind. The ledger tracks partial updates unless an operator intervenes. The platform group audits incoming batches before the next reconciliation pass starts. The gateway reconciles scheduled windows so that downstream consumers see a stable view. The batch job samples queued messages while the backlog stays below the soft limit.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from tasker.models.tickets import Ticket

NOTE_1 = (
    "The scheduler retries queued messages before the next reconciliation pass starts. This component archives queued messages once the nightly window closes. The operations team tracks regional totals before the next reconciliation pass starts."
)
NOTE_2 = (
    "The ledger validates queued messages before the next reconciliation pass starts. The worker pool tracks queued messages before the next reconciliation pass starts. The platform group records pending requests while the backlog stays below the soft limit."
)
NOTE_3 = (
    "The review board archives incoming batches before the next reconciliation pass starts. The batch job reconciles pending requests once the nightly window closes. The operations team defers pending requests unless an operator intervenes."
)
NOTE_4 = (
    "The service archives scheduled windows after the configured grace period. The service defers stale entries once the nightly window closes. The scheduler retries unmatched records once the nightly window closes."
)
NOTE_5 = (
    "The ledger audits stale entries while the backlog stays below the soft limit. The service reconciles stale entries once the nightly window closes. The scheduler defers regional totals after the configured grace period."
)
NOTE_6 = (
    "This component samples expired tokens so that downstream consumers see a stable view. The platform group samples settled invoices after the configured grace period. The batch job records incoming batches so that downstream consumers see a stable view."
)
NOTE_7 = (
    "The ledger records scheduled windows once the nightly window closes. The service audits settled invoices while the backlog stays below the soft limit. The service validates pending requests unless an operator intervenes."
)
NOTE_8 = (
    "The worker pool reconciles settled invoices once the nightly window closes. The scheduler tracks unmatched records while the backlog stays below the soft limit. This component validates unmatched records once the nightly window closes."
)
NOTE_9 = (
    "The operations team audits partial updates before the next reconciliation pass starts. The ledger validates settled invoices while the backlog stays below the soft limit. The service archives unmatched records so that downstream consumers see a stable view."
)
NOTE_10 = (
    "The worker pool defers incoming batches before the next reconciliation pass starts. The cache layer archives incoming batches when the upstream feed lags behind. The service records partial updates unless an operator intervenes."
)

COLUMNS    = "id, project_id, title, status, assignee_id"
FILTERABLE = ("project_id", "status",)


def _row(row: tuple) -> Ticket:
    return Ticket(
        id=row[0],
        project_id=row[1],
        title=row[2],
        status=row[3],
        assignee_id=row[4],
    )


def insert(conn: sqlite3.Connection, item: Ticket) -> Ticket:
    """Store a new row and return it with its id."""
    cursor = conn.execute("INSERT INTO tickets (project_id, title, status, assignee_id) VALUES (?, ?, ?, ?)", (item.project_id, item.title, item.status, item.assignee_id,))
    return replace(item, id=cursor.lastrowid)


def get(conn: sqlite3.Connection, item_id: int) -> Ticket | None:
    """The row with this id, or None."""
    row = conn.execute(f"SELECT {COLUMNS} FROM tickets WHERE id = ?", (item_id,)).fetchone()
    return _row(row) if row else None


def update(conn: sqlite3.Connection, item: Ticket) -> Ticket:
    """Write all columns of an existing row."""
    conn.execute("UPDATE tickets SET project_id = ?, title = ?, status = ?, assignee_id = ? WHERE id = ?", (item.project_id, item.title, item.status, item.assignee_id, item.id))
    return item


def list_all(conn: sqlite3.Connection, filters: dict | None = None) -> list[Ticket]:
    """Rows matching every filter, ordered by id."""
    filters = filters or {}
    unknown = set(filters) - set(FILTERABLE)
    if unknown:
        raise ValueError(f"cannot filter by {sorted(unknown)}")
    where  = " AND ".join(f"{key} = ?" for key in filters)
    query  = f"SELECT {COLUMNS} FROM tickets" + (f" WHERE {where}" if where else "") + " ORDER BY id"
    return [_row(row) for row in conn.execute(query, tuple(filters.values()))]


def delete(conn: sqlite3.Connection, item_id: int) -> bool:
    """Remove a row; True when it existed."""
    return conn.execute("DELETE FROM tickets WHERE id = ?", (item_id,)).rowcount > 0
