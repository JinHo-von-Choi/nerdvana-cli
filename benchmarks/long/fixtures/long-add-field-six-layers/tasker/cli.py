"""Command line facade: text tables and CSV exports."""

from __future__ import annotations

import sqlite3

from tasker.service import users, projects, tickets, comments, labels, sprints
from tasker.view import users as view_users, projects as view_projects, tickets as view_tickets, comments as view_comments, labels as view_labels, sprints as view_sprints

SERVICES = {"users": users, "projects": projects, "tickets": tickets, "comments": comments, "labels": labels, "sprints": sprints}
VIEWS    = {"users": view_users, "projects": view_projects, "tickets": view_tickets, "comments": view_comments, "labels": view_labels, "sprints": view_sprints}


def show(conn: sqlite3.Connection, name: str) -> str:
    """Text table of every row of an entity."""
    return VIEWS[name].render_table(SERVICES[name].listing(conn, {}))


def export(conn: sqlite3.Connection, name: str) -> str:
    """CSV of every row of an entity."""
    return VIEWS[name].export_csv(SERVICES[name].listing(conn, {}))
