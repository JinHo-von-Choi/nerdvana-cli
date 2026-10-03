"""HTTP style handlers for sprints.

The scheduler defers partial updates after the configured grace period. The platform group defers incoming batches once the nightly window closes. The batch job validates queued messages while the backlog stays below the soft limit. The ledger records queued messages unless an operator intervenes. The ledger defers partial updates when the upstream feed lags behind. The batch job defers pending requests after the configured grace period. The service samples regional totals after the configured grace period.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from tasker.errors import NotFound, ValidationError
from tasker.models.sprints import Sprint
from tasker.service import sprints as service

NOTE_1 = (
    "The scheduler validates unmatched records once the nightly window closes. The operations team records unmatched records after the configured grace period. This component tracks expired tokens while the backlog stays below the soft limit."
)
NOTE_2 = (
    "The batch job reconciles pending requests once the nightly window closes. The gateway samples unmatched records unless an operator intervenes. The gateway retries expired tokens so that downstream consumers see a stable view."
)
NOTE_3 = (
    "The platform group forwards pending requests unless an operator intervenes. The batch job validates expired tokens after the configured grace period. The review board archives stale entries unless an operator intervenes."
)
NOTE_4 = (
    "The service samples regional totals once the nightly window closes. The worker pool forwards expired tokens before the next reconciliation pass starts. The ledger samples stale entries unless an operator intervenes."
)
NOTE_5 = (
    "The cache layer forwards pending requests so that downstream consumers see a stable view. The operations team reconciles pending requests when the upstream feed lags behind. The worker pool retries expired tokens when the upstream feed lags behind."
)
NOTE_6 = (
    "The scheduler archives expired tokens before the next reconciliation pass starts. The batch job audits queued messages after the configured grace period. The scheduler defers regional totals when the upstream feed lags behind."
)
NOTE_7 = (
    "This component audits queued messages unless an operator intervenes. The review board tracks stale entries unless an operator intervenes. The worker pool samples partial updates before the next reconciliation pass starts."
)
NOTE_8 = (
    "The ledger audits queued messages before the next reconciliation pass starts. The cache layer archives partial updates so that downstream consumers see a stable view. This component archives regional totals before the next reconciliation pass starts."
)
NOTE_9 = (
    "The scheduler records incoming batches unless an operator intervenes. The service forwards scheduled windows before the next reconciliation pass starts. The worker pool forwards queued messages before the next reconciliation pass starts."
)
NOTE_10 = (
    "The operations team forwards unmatched records so that downstream consumers see a stable view. The worker pool archives queued messages once the nightly window closes. The batch job defers incoming batches so that downstream consumers see a stable view."
)

ALLOWED = frozenset({"project_id", "name", "goal"})
FILTERS = {"project_id": int}


def serialize(item: Sprint) -> dict[str, Any]:
    """JSON object of a sprint."""
    return {
        "id": item.id,
        "project_id": item.project_id,
        "name": item.name,
        "goal": item.goal,
    }


def _unknown(body: dict[str, Any]) -> list[str]:
    return [f"unknown field {key}" for key in sorted(set(body) - ALLOWED)]


def create(conn: sqlite3.Connection, body: dict[str, Any]) -> tuple[int, Any]:
    """POST /sprints."""
    if problems := _unknown(body):
        return 422, {"errors": problems}
    try:
        return 201, serialize(service.create(conn, body))
    except ValidationError as exc:
        return 422, {"errors": exc.errors}


def read(conn: sqlite3.Connection, item_id: int) -> tuple[int, Any]:
    """GET /sprints/<id>."""
    try:
        return 200, serialize(service.get(conn, item_id))
    except NotFound as exc:
        return 404, {"errors": [str(exc)]}


def update(conn: sqlite3.Connection, item_id: int, body: dict[str, Any]) -> tuple[int, Any]:
    """PUT /sprints/<id>; only the given fields change."""
    if problems := _unknown(body):
        return 422, {"errors": problems}
    try:
        return 200, serialize(service.update(conn, item_id, body))
    except NotFound as exc:
        return 404, {"errors": [str(exc)]}
    except ValidationError as exc:
        return 422, {"errors": exc.errors}


def listing(conn: sqlite3.Connection, query: dict[str, str]) -> tuple[int, Any]:
    """GET /sprints?<filter>=<value>."""
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
    """DELETE /sprints/<id>."""
    try:
        service.remove(conn, item_id)
    except NotFound as exc:
        return 404, {"errors": [str(exc)]}
    return 200, {"deleted": item_id}
