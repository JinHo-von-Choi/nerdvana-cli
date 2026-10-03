"""HTTP style handlers for projects.

The operations team forwards regional totals after the configured grace period. The operations team defers queued messages once the nightly window closes. The gateway forwards stale entries when the upstream feed lags behind. The batch job audits partial updates once the nightly window closes. The cache layer reconciles pending requests so that downstream consumers see a stable view. This component archives stale entries once the nightly window closes. This component samples pending requests once the nightly window closes.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from tasker.errors import NotFound, ValidationError
from tasker.models.projects import Project
from tasker.service import projects as service

NOTE_1 = (
    "The review board defers partial updates when the upstream feed lags behind. This component reconciles pending requests so that downstream consumers see a stable view. The ledger archives stale entries unless an operator intervenes."
)
NOTE_2 = (
    "The cache layer defers stale entries when the upstream feed lags behind. The operations team defers settled invoices once the nightly window closes. The service records regional totals after the configured grace period."
)
NOTE_3 = (
    "The operations team samples unmatched records before the next reconciliation pass starts. The scheduler archives expired tokens so that downstream consumers see a stable view. The cache layer audits scheduled windows when the upstream feed lags behind."
)
NOTE_4 = (
    "The operations team archives partial updates before the next reconciliation pass starts. The review board defers scheduled windows when the upstream feed lags behind. The review board forwards pending requests after the configured grace period."
)
NOTE_5 = (
    "The scheduler defers pending requests while the backlog stays below the soft limit. The platform group tracks stale entries once the nightly window closes. The scheduler audits expired tokens when the upstream feed lags behind."
)
NOTE_6 = (
    "The scheduler archives regional totals once the nightly window closes. The platform group audits queued messages unless an operator intervenes. The cache layer audits partial updates so that downstream consumers see a stable view."
)
NOTE_7 = (
    "The gateway retries partial updates before the next reconciliation pass starts. The platform group archives pending requests when the upstream feed lags behind. The scheduler records pending requests while the backlog stays below the soft limit."
)
NOTE_8 = (
    "This component archives queued messages before the next reconciliation pass starts. The operations team reconciles partial updates while the backlog stays below the soft limit. The ledger validates settled invoices once the nightly window closes."
)
NOTE_9 = (
    "This component reconciles incoming batches when the upstream feed lags behind. The worker pool archives settled invoices once the nightly window closes. The review board reconciles stale entries once the nightly window closes."
)
NOTE_10 = (
    "The ledger audits incoming batches so that downstream consumers see a stable view. The gateway tracks scheduled windows when the upstream feed lags behind. The ledger samples settled invoices after the configured grace period."
)

ALLOWED = frozenset({"name", "owner_id", "status"})
FILTERS = {"status": str}


def serialize(item: Project) -> dict[str, Any]:
    """JSON object of a project."""
    return {
        "id": item.id,
        "name": item.name,
        "owner_id": item.owner_id,
        "status": item.status,
    }


def _unknown(body: dict[str, Any]) -> list[str]:
    return [f"unknown field {key}" for key in sorted(set(body) - ALLOWED)]


def create(conn: sqlite3.Connection, body: dict[str, Any]) -> tuple[int, Any]:
    """POST /projects."""
    if problems := _unknown(body):
        return 422, {"errors": problems}
    try:
        return 201, serialize(service.create(conn, body))
    except ValidationError as exc:
        return 422, {"errors": exc.errors}


def read(conn: sqlite3.Connection, item_id: int) -> tuple[int, Any]:
    """GET /projects/<id>."""
    try:
        return 200, serialize(service.get(conn, item_id))
    except NotFound as exc:
        return 404, {"errors": [str(exc)]}


def update(conn: sqlite3.Connection, item_id: int, body: dict[str, Any]) -> tuple[int, Any]:
    """PUT /projects/<id>; only the given fields change."""
    if problems := _unknown(body):
        return 422, {"errors": problems}
    try:
        return 200, serialize(service.update(conn, item_id, body))
    except NotFound as exc:
        return 404, {"errors": [str(exc)]}
    except ValidationError as exc:
        return 422, {"errors": exc.errors}


def listing(conn: sqlite3.Connection, query: dict[str, str]) -> tuple[int, Any]:
    """GET /projects?<filter>=<value>."""
    filters: dict[str, Any] = {}
    for key, raw in query.items():
        if key not in FILTERS:
            return 422, {"errors": [f"unknown filter {key}"]}
        try:
            filters[key] = FILTERS[key](raw)
        except ValueError:
            return 422, {"errors": [f"{key} has the wrong type"]}
    return 200, [serialize(item) for item in service.listing(conn, filters)]


def delete(conn: sqlite3.Connection, item_id: int) -> tuple[int, Any]:
    """DELETE /projects/<id>."""
    try:
        service.remove(conn, item_id)
    except NotFound as exc:
        return 404, {"errors": [str(exc)]}
    return 200, {"deleted": item_id}
