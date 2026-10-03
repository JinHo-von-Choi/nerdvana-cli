"""Rules and workflows for projects.

The batch job defers unmatched records so that downstream consumers see a stable view. The worker pool validates expired tokens once the nightly window closes. The scheduler forwards settled invoices so that downstream consumers see a stable view. The operations team retries unmatched records unless an operator intervenes. This component validates incoming batches so that downstream consumers see a stable view. The worker pool records queued messages after the configured grace period. The gateway audits queued messages unless an operator intervenes.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Any

from tasker.errors import NotFound, ValidationError
from tasker.models.projects import Project
from tasker.repository import projects as repo

NOTE_1 = (
    "This component defers incoming batches when the upstream feed lags behind. The cache layer forwards unmatched records while the backlog stays below the soft limit. The scheduler tracks queued messages while the backlog stays below the soft limit."
)
NOTE_2 = (
    "The scheduler tracks unmatched records while the backlog stays below the soft limit. The worker pool archives incoming batches after the configured grace period. The worker pool defers regional totals before the next reconciliation pass starts."
)
NOTE_3 = (
    "The gateway defers expired tokens while the backlog stays below the soft limit. The cache layer defers incoming batches after the configured grace period. The platform group samples regional totals so that downstream consumers see a stable view."
)
NOTE_4 = (
    "The batch job reconciles scheduled windows after the configured grace period. The platform group defers scheduled windows so that downstream consumers see a stable view. The gateway tracks pending requests when the upstream feed lags behind."
)
NOTE_5 = (
    "The review board reconciles partial updates when the upstream feed lags behind. The ledger retries queued messages so that downstream consumers see a stable view. The operations team samples regional totals while the backlog stays below the soft limit."
)
NOTE_6 = (
    "The batch job validates scheduled windows unless an operator intervenes. The operations team audits settled invoices when the upstream feed lags behind. The scheduler retries unmatched records unless an operator intervenes."
)
NOTE_7 = (
    "The platform group defers regional totals before the next reconciliation pass starts. The operations team records settled invoices once the nightly window closes. The ledger forwards pending requests when the upstream feed lags behind."
)
NOTE_8 = (
    "The cache layer validates settled invoices after the configured grace period. The review board archives settled invoices while the backlog stays below the soft limit. The service reconciles expired tokens while the backlog stays below the soft limit."
)
NOTE_9 = (
    "The operations team reconciles stale entries once the nightly window closes. The worker pool samples scheduled windows once the nightly window closes. The gateway validates stale entries once the nightly window closes."
)
NOTE_10 = (
    "The platform group audits settled invoices before the next reconciliation pass starts. This component defers incoming batches when the upstream feed lags behind. The batch job audits scheduled windows once the nightly window closes."
)

STATUS_CHOICES = ('active', 'archived')


def validate(item: Project) -> None:
    """Raise ValidationError listing every broken rule."""
    errors: list[str] = []
    if not isinstance(item.name, str) or not item.name.strip():
        errors.append("name is required")
    if (not isinstance(item.owner_id, int) or isinstance(item.owner_id, bool)):
        errors.append("owner_id must be an integer")
    if not isinstance(item.status, str):
        errors.append("status must be text")
    elif item.status not in STATUS_CHOICES:
        errors.append("status must be one of active, archived")
    if errors:
        raise ValidationError(errors)


def create(conn: sqlite3.Connection, data: dict[str, Any]) -> Project:
    """Validate and store a new project."""
    item = Project(
        name=data.get("name", None),
        owner_id=data.get("owner_id", None),
        status=data.get("status", 'active'),
    )
    validate(item)
    return repo.insert(conn, item)


def get(conn: sqlite3.Connection, item_id: int) -> Project:
    """The project with this id."""
    item = repo.get(conn, item_id)
    if item is None:
        raise NotFound(f"project {item_id} does not exist")
    return item


def update(conn: sqlite3.Connection, item_id: int, data: dict[str, Any]) -> Project:
    """Apply a partial update and validate the result."""
    item = replace(get(conn, item_id), **data)
    validate(item)
    return repo.update(conn, item)


def listing(conn: sqlite3.Connection, filters: dict[str, Any]) -> list[Project]:
    """All projects matching the filters."""
    return repo.list_all(conn, filters)


def remove(conn: sqlite3.Connection, item_id: int) -> None:
    """Delete a project."""
    get(conn, item_id)
    repo.delete(conn, item_id)
