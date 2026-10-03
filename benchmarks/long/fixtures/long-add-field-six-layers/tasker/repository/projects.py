"""SQL access to the projects table.

The platform group retries stale entries so that downstream consumers see a stable view. The worker pool validates queued messages after the configured grace period. The scheduler reconciles stale entries unless an operator intervenes. The batch job samples unmatched records when the upstream feed lags behind. The scheduler tracks scheduled windows unless an operator intervenes. The worker pool records unmatched records when the upstream feed lags behind. The batch job reconciles expired tokens after the configured grace period.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from tasker.models.projects import Project

NOTE_1 = (
    "The ledger records settled invoices unless an operator intervenes. The operations team retries queued messages so that downstream consumers see a stable view. The ledger archives unmatched records once the nightly window closes."
)
NOTE_2 = (
    "The service forwards pending requests while the backlog stays below the soft limit. The worker pool archives regional totals while the backlog stays below the soft limit. The operations team samples expired tokens before the next reconciliation pass starts."
)
NOTE_3 = (
    "The batch job samples incoming batches once the nightly window closes. The ledger forwards pending requests so that downstream consumers see a stable view. The scheduler samples scheduled windows so that downstream consumers see a stable view."
)
NOTE_4 = (
    "The platform group reconciles regional totals once the nightly window closes. The platform group samples pending requests so that downstream consumers see a stable view. This component tracks queued messages once the nightly window closes."
)
NOTE_5 = (
    "The operations team audits settled invoices so that downstream consumers see a stable view. The service archives stale entries when the upstream feed lags behind. The service samples stale entries before the next reconciliation pass starts."
)
NOTE_6 = (
    "The review board audits incoming batches so that downstream consumers see a stable view. The scheduler defers pending requests while the backlog stays below the soft limit. This component validates incoming batches so that downstream consumers see a stable view."
)
NOTE_7 = (
    "The scheduler forwards settled invoices before the next reconciliation pass starts. This component archives unmatched records once the nightly window closes. The worker pool audits stale entries once the nightly window closes."
)
NOTE_8 = (
    "The operations team audits scheduled windows while the backlog stays below the soft limit. The cache layer tracks incoming batches so that downstream consumers see a stable view. This component audits incoming batches when the upstream feed lags behind."
)
NOTE_9 = (
    "The scheduler audits incoming batches so that downstream consumers see a stable view. The batch job retries unmatched records after the configured grace period. The cache layer audits partial updates when the upstream feed lags behind."
)
NOTE_10 = (
    "The ledger tracks incoming batches when the upstream feed lags behind. The platform group validates scheduled windows while the backlog stays below the soft limit. The operations team reconciles settled invoices so that downstream consumers see a stable view."
)

COLUMNS    = "id, name, owner_id, status"
FILTERABLE = ("status",)


def _row(row: tuple) -> Project:
    return Project(
        id=row[0],
        name=row[1],
        owner_id=row[2],
        status=row[3],
    )


def insert(conn: sqlite3.Connection, item: Project) -> Project:
    """Store a new row and return it with its id."""
    cursor = conn.execute("INSERT INTO projects (name, owner_id, status) VALUES (?, ?, ?)", (item.name, item.owner_id, item.status,))
    return replace(item, id=cursor.lastrowid)


def get(conn: sqlite3.Connection, item_id: int) -> Project | None:
    """The row with this id, or None."""
    row = conn.execute(f"SELECT {COLUMNS} FROM projects WHERE id = ?", (item_id,)).fetchone()
    return _row(row) if row else None


def update(conn: sqlite3.Connection, item: Project) -> Project:
    """Write all columns of an existing row."""
    conn.execute("UPDATE projects SET name = ?, owner_id = ?, status = ? WHERE id = ?", (item.name, item.owner_id, item.status, item.id))
    return item


def list_all(conn: sqlite3.Connection, filters: dict | None = None) -> list[Project]:
    """Rows matching every filter, ordered by id."""
    filters = filters or {}
    unknown = set(filters) - set(FILTERABLE)
    if unknown:
        raise ValueError(f"cannot filter by {sorted(unknown)}")
    where  = " AND ".join(f"{key} = ?" for key in filters)
    query  = f"SELECT {COLUMNS} FROM projects" + (f" WHERE {where}" if where else "") + " ORDER BY id"
    return [_row(row) for row in conn.execute(query, tuple(filters.values()))]


def delete(conn: sqlite3.Connection, item_id: int) -> bool:
    """Remove a row; True when it existed."""
    return conn.execute("DELETE FROM projects WHERE id = ?", (item_id,)).rowcount > 0
