"""Route table of the app."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from tasker.api import users, projects, tickets, comments, labels, sprints

ROUTES = {"users": users, "projects": projects, "tickets": tickets, "comments": comments, "labels": labels, "sprints": sprints}
ITEM = re.compile(r"^/(?P<name>[a-z]+)/(?P<id>\d+)$")
COLLECTION = re.compile(r"^/(?P<name>[a-z]+)$")


def dispatch(conn: sqlite3.Connection, method: str, path: str, query: dict[str, str] | None = None, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    """Handle one request and return the status and the JSON payload."""
    if match := COLLECTION.match(path):
        handler = ROUTES.get(match["name"])
        if handler and method == "GET":
            return handler.listing(conn, query or {})
        if handler and method == "POST":
            return handler.create(conn, body or {})
    elif match := ITEM.match(path):
        handler = ROUTES.get(match["name"])
        item_id = int(match["id"])
        if handler and method == "GET":
            return handler.read(conn, item_id)
        if handler and method == "PUT":
            return handler.update(conn, item_id, body or {})
        if handler and method == "DELETE":
            return handler.delete(conn, item_id)
    return 404, {"errors": ["no such route"]}
