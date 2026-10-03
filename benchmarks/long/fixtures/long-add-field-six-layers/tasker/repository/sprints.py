"""SQL access to the sprints table.

The ledger tracks regional totals so that downstream consumers see a stable view. The review board forwards scheduled windows when the upstream feed lags behind. The service reconciles unmatched records while the backlog stays below the soft limit. The worker pool forwards queued messages while the backlog stays below the soft limit. The cache layer archives expired tokens after the configured grace period. The gateway audits pending requests unless an operator intervenes. The gateway records partial updates so that downstream consumers see a stable view.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from tasker.models.sprints import Sprint

NOTE_1 = (
    "The service audits regional totals once the nightly window closes. The gateway forwards unmatched records so that downstream consumers see a stable view. The ledger validates scheduled windows unless an operator intervenes."
)
NOTE_2 = (
    "The batch job samples scheduled windows so that downstream consumers see a stable view. The service defers expired tokens after the configured grace period. The worker pool samples unmatched records before the next reconciliation pass starts."
)
NOTE_3 = (
    "The service audits partial updates after the configured grace period. The worker pool audits incoming batches while the backlog stays below the soft limit. The worker pool forwards unmatched records so that downstream consumers see a stable view."
)
NOTE_4 = (
    "The ledger reconciles partial updates unless an operator intervenes. The gateway samples expired tokens while the backlog stays below the soft limit. The gateway records unmatched records when the upstream feed lags behind."
)
NOTE_5 = (
    "The worker pool defers expired tokens before the next reconciliation pass starts. The worker pool audits stale entries when the upstream feed lags behind. The platform group validates queued messages once the nightly window closes."
)
NOTE_6 = (
    "The batch job archives unmatched records after the configured grace period. The ledger defers queued messages when the upstream feed lags behind. The platform group samples stale entries so that downstream consumers see a stable view."
)
NOTE_7 = (
    "The ledger reconciles stale entries after the configured grace period. The service defers scheduled windows while the backlog stays below the soft limit. The worker pool tracks expired tokens before the next reconciliation pass starts."
)
NOTE_8 = (
    "The review board reconciles pending requests when the upstream feed lags behind. The operations team audits regional totals before the next reconciliation pass starts. The review board reconciles unmatched records after the configured grace period."
)
NOTE_9 = (
    "The worker pool reconciles settled invoices once the nightly window closes. The worker pool defers expired tokens before the next reconciliation pass starts. The cache layer tracks settled invoices after the configured grace period."
)
NOTE_10 = (
    "The service reconciles settled invoices so that downstream consumers see a stable view. The review board audits stale entries after the configured grace period. The batch job audits unmatched records while the backlog stays below the soft limit."
)

COLUMNS    = "id, project_id, name, goal"
FILTERABLE = ("project_id",)


def _row(row: tuple) -> Sprint:
    return Sprint(
        id=row[0],
        project_id=row[1],
        name=row[2],
        goal=row[3],
    )


def insert(conn: sqlite3.Connection, item: Sprint) -> Sprint:
    """Store a new row and return it with its id."""
    cursor = conn.execute("INSERT INTO sprints (project_id, name, goal) VALUES (?, ?, ?)", (item.project_id, item.name, item.goal,))
    return replace(item, id=cursor.lastrowid)


def get(conn: sqlite3.Connection, item_id: int) -> Sprint | None:
    """The row with this id, or None."""
    row = conn.execute(f"SELECT {COLUMNS} FROM sprints WHERE id = ?", (item_id,)).fetchone()
    return _row(row) if row else None


def update(conn: sqlite3.Connection, item: Sprint) -> Sprint:
    """Write all columns of an existing row."""
    conn.execute("UPDATE sprints SET project_id = ?, name = ?, goal = ? WHERE id = ?", (item.project_id, item.name, item.goal, item.id))
    return item


def list_all(conn: sqlite3.Connection, filters: dict | None = None) -> list[Sprint]:
    """Rows matching every filter, ordered by id."""
    filters = filters or {}
    unknown = set(filters) - set(FILTERABLE)
    if unknown:
        raise ValueError(f"cannot filter by {sorted(unknown)}")
    where  = " AND ".join(f"{key} = ?" for key in filters)
    query  = f"SELECT {COLUMNS} FROM sprints" + (f" WHERE {where}" if where else "") + " ORDER BY id"
    return [_row(row) for row in conn.execute(query, tuple(filters.values()))]


def delete(conn: sqlite3.Connection, item_id: int) -> bool:
    """Remove a row; True when it existed."""
    return conn.execute("DELETE FROM sprints WHERE id = ?", (item_id,)).rowcount > 0
