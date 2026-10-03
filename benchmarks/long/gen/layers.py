"""Task long-add-field-six-layers: add one field to a small app that has six entities and seven layers.

Author: 최진호
Date:   2026-10-03

The app is rendered from entity specifications. The task's starting repository is the app without a
``severity`` field on tickets; the solution overlay is the app with it, so the overlay holds exactly
the files that a correct change touches (migration, model, repository, service, api, view, openapi).
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field, replace
from typing import Any

from .common import TaskBuild, prose, rng_for

TASK_ID = "long-add-field-six-layers"


@dataclass(frozen=True)
class Field:
    """One column of an entity."""

    name:       str
    kind:       str = "str"
    required:   bool = True
    default:    Any = None
    choices:    tuple[str, ...] = ()
    low:        int | None = None
    high:       int | None = None
    email:      bool = False
    filterable: bool = False


@dataclass(frozen=True)
class Entity:
    """One resource of the app."""

    cls:    str
    table:  str
    fields: tuple[Field, ...]
    sample: dict[str, Any] = field(default_factory=dict)
    patch:  dict[str, Any] = field(default_factory=dict)


SEVERITY = Field("severity", "int", False, 3, low=1, high=5, filterable=True)

TICKETS_BASE = Entity("Ticket", "tickets", (
    Field("project_id", "int", filterable=True), Field("title"), Field("status", required=False, default="open", choices=("open", "doing", "done"), filterable=True),
    Field("assignee_id", "int", False)), {"project_id": 1, "title": "Fix login", "status": "open"}, {"status": "doing"})

OTHERS = [
    Entity("User", "users", (Field("name"), Field("email", email=True), Field("role", required=False, default="member", choices=("admin", "member", "guest"), filterable=True)),
           {"name": "Ada", "email": "ada@example.com"}, {"role": "admin"}),
    Entity("Project", "projects", (Field("name"), Field("owner_id", "int"), Field("status", required=False, default="active", choices=("active", "archived"), filterable=True)),
           {"name": "Apollo", "owner_id": 1}, {"status": "archived"}),
    Entity("Comment", "comments", (Field("ticket_id", "int", filterable=True), Field("author_id", "int"), Field("body")),
           {"ticket_id": 1, "author_id": 1, "body": "Looks good"}, {"body": "Needs work"}),
    Entity("Label", "labels", (Field("name"), Field("color", required=False, default="gray", choices=("gray", "red", "green", "blue"), filterable=True)),
           {"name": "bug"}, {"color": "red"}),
    Entity("Sprint", "sprints", (Field("project_id", "int", filterable=True), Field("name"), Field("goal", required=False, default="")),
           {"project_id": 1, "name": "Sprint 1"}, {"goal": "Ship it"}),
]


def entities(with_severity: bool) -> list[Entity]:
    """All entities; the ticket entity carries the severity field only in the finished app."""
    tickets = replace(TICKETS_BASE, fields=TICKETS_BASE.fields + ((SEVERITY,) if with_severity else ()))
    return [OTHERS[0], OTHERS[1], tickets, *OTHERS[2:]]


def _notes(rng: random.Random) -> str:
    return "\n".join(f'NOTE_{i + 1} = (\n    "{prose(rng, 3)}"\n)' for i in range(10)) + "\n"


def _doc(rng: random.Random, title: str) -> str:
    return f'"""{title}\n\n{prose(rng, 7)}\n"""\n\nfrom __future__ import annotations\n'


def _sql_type(f: Field) -> str:
    return "TEXT" if f.kind == "str" else "INTEGER"


def migration(e: Entity) -> str:
    cols = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
    for f in e.fields:
        line = f"{f.name} {_sql_type(f)}"
        if f.required or f.default is not None:
            line += " NOT NULL"
        if f.default is not None:
            line += f" DEFAULT {f.default!r}" if f.kind == "int" else f" DEFAULT '{f.default}'"
        cols.append(line)
    return f"CREATE TABLE {e.table} (\n    " + ",\n    ".join(cols) + "\n);\n"


def model(rng: random.Random, e: Entity) -> str:
    plain = [f for f in e.fields if f.required and f.default is None]
    rest  = [f for f in e.fields if f not in plain]
    py    = lambda f: "str" if f.kind == "str" else "int"  # noqa: E731
    lines = [f"    {f.name}: {py(f)}" for f in plain]
    lines += [f"    {f.name}: {py(f)} | None = None" if f.default is None else f"    {f.name}: {py(f)} = {f.default!r}" for f in rest]
    lines.append("    id: int | None = None")
    return (_doc(rng, f"The {e.cls} record.") + "\nfrom dataclasses import dataclass\n\n" + _notes(rng)
            + f"\n\n@dataclass\nclass {e.cls}:\n    \"\"\"One row of the {e.table} table.\"\"\"\n\n" + "\n".join(lines) + "\n")


def repository(rng: random.Random, e: Entity) -> str:
    names  = [f.name for f in e.fields]
    marks  = ", ".join("?" for _ in names)
    params = ", ".join(f"item.{n}" for n in names)
    sets   = ", ".join(f"{n} = ?" for n in names)
    rows   = ",\n        ".join(f"{n}=row[{i + 1}]" for i, n in enumerate(names))
    filt   = ", ".join(f'"{f.name}"' for f in e.fields if f.filterable)
    return (_doc(rng, f"SQL access to the {e.table} table.") + f"\nimport sqlite3\nfrom dataclasses import replace\n\nfrom tasker.models.{e.table} import {e.cls}\n\n" + _notes(rng)
            + f'''
COLUMNS    = "id, {", ".join(names)}"
FILTERABLE = ({filt},)


def _row(row: tuple) -> {e.cls}:
    return {e.cls}(
        id=row[0],
        {rows},
    )


def insert(conn: sqlite3.Connection, item: {e.cls}) -> {e.cls}:
    """Store a new row and return it with its id."""
    cursor = conn.execute("INSERT INTO {e.table} ({", ".join(names)}) VALUES ({marks})", ({params},))
    return replace(item, id=cursor.lastrowid)


def get(conn: sqlite3.Connection, item_id: int) -> {e.cls} | None:
    """The row with this id, or None."""
    row = conn.execute(f"SELECT {{COLUMNS}} FROM {e.table} WHERE id = ?", (item_id,)).fetchone()
    return _row(row) if row else None


def update(conn: sqlite3.Connection, item: {e.cls}) -> {e.cls}:
    """Write all columns of an existing row."""
    conn.execute("UPDATE {e.table} SET {sets} WHERE id = ?", ({params}, item.id))
    return item


def list_all(conn: sqlite3.Connection, filters: dict | None = None) -> list[{e.cls}]:
    """Rows matching every filter, ordered by id."""
    filters = filters or {{}}
    unknown = set(filters) - set(FILTERABLE)
    if unknown:
        raise ValueError(f"cannot filter by {{sorted(unknown)}}")
    where  = " AND ".join(f"{{key}} = ?" for key in filters)
    query  = f"SELECT {{COLUMNS}} FROM {e.table}" + (f" WHERE {{where}}" if where else "") + " ORDER BY id"
    return [_row(row) for row in conn.execute(query, tuple(filters.values()))]


def delete(conn: sqlite3.Connection, item_id: int) -> bool:
    """Remove a row; True when it existed."""
    return conn.execute("DELETE FROM {e.table} WHERE id = ?", (item_id,)).rowcount > 0
''')


def _rules(f: Field) -> list[str]:
    n = f"item.{f.name}"
    if f.kind == "str" and f.required:
        out = [f'    if not isinstance({n}, str) or not {n}.strip():', f'        errors.append("{f.name} is required")']
    elif f.kind == "str":
        out = [f'    if not isinstance({n}, str):', f'        errors.append("{f.name} must be text")']
    else:
        guard = f"{n} is not None and " if not f.required and f.default is None else ""
        out = [f'    if {guard}(not isinstance({n}, int) or isinstance({n}, bool)):', f'        errors.append("{f.name} must be an integer")']
    if f.choices:
        out += [f"    elif {n} not in {f.name.upper()}_CHOICES:", f'        errors.append("{f.name} must be one of {", ".join(f.choices)}")']
    if f.low is not None:
        out += [f"    elif not {f.low} <= {n} <= {f.high}:", f'        errors.append("{f.name} must be between {f.low} and {f.high}")']
    if f.email:
        out += [f'    elif "@" not in {n}:', f'        errors.append("{f.name} is invalid")']
    return out


def service(rng: random.Random, e: Entity) -> str:
    consts = "".join(f"{f.name.upper()}_CHOICES = {f.choices!r}\n" for f in e.fields if f.choices)
    rules  = "\n".join(line for f in e.fields for line in _rules(f))
    build  = ",\n        ".join(f"{f.name}=data.get(\"{f.name}\", {f.default!r})" for f in e.fields)
    return (_doc(rng, f"Rules and workflows for {e.table}.") + f"\nimport sqlite3\nfrom dataclasses import replace\nfrom typing import Any\n\nfrom tasker.errors import NotFound, ValidationError\n"
            f"from tasker.models.{e.table} import {e.cls}\nfrom tasker.repository import {e.table} as repo\n\n" + _notes(rng) + "\n" + consts + f'''

def validate(item: {e.cls}) -> None:
    """Raise ValidationError listing every broken rule."""
    errors: list[str] = []
{rules}
    if errors:
        raise ValidationError(errors)


def create(conn: sqlite3.Connection, data: dict[str, Any]) -> {e.cls}:
    """Validate and store a new {e.cls.lower()}."""
    item = {e.cls}(
        {build},
    )
    validate(item)
    return repo.insert(conn, item)


def get(conn: sqlite3.Connection, item_id: int) -> {e.cls}:
    """The {e.cls.lower()} with this id."""
    item = repo.get(conn, item_id)
    if item is None:
        raise NotFound(f"{e.cls.lower()} {{item_id}} does not exist")
    return item


def update(conn: sqlite3.Connection, item_id: int, data: dict[str, Any]) -> {e.cls}:
    """Apply a partial update and validate the result."""
    item = replace(get(conn, item_id), **data)
    validate(item)
    return repo.update(conn, item)


def listing(conn: sqlite3.Connection, filters: dict[str, Any]) -> list[{e.cls}]:
    """All {e.table} matching the filters."""
    return repo.list_all(conn, filters)


def remove(conn: sqlite3.Connection, item_id: int) -> None:
    """Delete a {e.cls.lower()}."""
    get(conn, item_id)
    repo.delete(conn, item_id)
''')


def api(rng: random.Random, e: Entity) -> str:
    names   = [f.name for f in e.fields]
    pairs   = ",\n        ".join(f'"{n}": item.{n}' for n in names)
    parsers = ", ".join(f'"{f.name}": {f.kind}' for f in e.fields if f.filterable)
    return (_doc(rng, f"HTTP style handlers for {e.table}.") + f"\nimport sqlite3\nfrom typing import Any\n\nfrom tasker.errors import NotFound, ValidationError\n"
            f"from tasker.models.{e.table} import {e.cls}\nfrom tasker.service import {e.table} as service\n\n" + _notes(rng) + f'''
ALLOWED = frozenset({{{", ".join(f'"{n}"' for n in names)}}})
FILTERS = {{{parsers}}}


def serialize(item: {e.cls}) -> dict[str, Any]:
    """JSON object of a {e.cls.lower()}."""
    return {{
        "id": item.id,
        {pairs},
    }}


def _unknown(body: dict[str, Any]) -> list[str]:
    return [f"unknown field {{key}}" for key in sorted(set(body) - ALLOWED)]


def create(conn: sqlite3.Connection, body: dict[str, Any]) -> tuple[int, Any]:
    """POST /{e.table}."""
    if problems := _unknown(body):
        return 422, {{"errors": problems}}
    try:
        return 201, serialize(service.create(conn, body))
    except ValidationError as exc:
        return 422, {{"errors": exc.errors}}


def read(conn: sqlite3.Connection, item_id: int) -> tuple[int, Any]:
    """GET /{e.table}/<id>."""
    try:
        return 200, serialize(service.get(conn, item_id))
    except NotFound as exc:
        return 404, {{"errors": [str(exc)]}}


def update(conn: sqlite3.Connection, item_id: int, body: dict[str, Any]) -> tuple[int, Any]:
    """PUT /{e.table}/<id>; only the given fields change."""
    if problems := _unknown(body):
        return 422, {{"errors": problems}}
    try:
        return 200, serialize(service.update(conn, item_id, body))
    except NotFound as exc:
        return 404, {{"errors": [str(exc)]}}
    except ValidationError as exc:
        return 422, {{"errors": exc.errors}}


def listing(conn: sqlite3.Connection, query: dict[str, str]) -> tuple[int, Any]:
    """GET /{e.table}?<filter>=<value>."""
    filters: dict[str, Any] = {{}}
    for key, raw in query.items():
        if key not in FILTERS:
            return 422, {{"errors": [f"unknown filter {{key}}"]}}
        try:
            filters[key] = FILTERS[key](raw)
        except ValueError:
            return 422, {{"errors": [f"{{key}} has the wrong type"]}}
    return 200, [serialize(item) for item in service.listing(conn, filters)]


def delete(conn: sqlite3.Connection, item_id: int) -> tuple[int, Any]:
    """DELETE /{e.table}/<id>."""
    try:
        service.remove(conn, item_id)
    except NotFound as exc:
        return 404, {{"errors": [str(exc)]}}
    return 200, {{"deleted": item_id}}
''')


def view(rng: random.Random, e: Entity) -> str:
    names = ["id"] + [f.name for f in e.fields]
    cells = ", ".join(f"item.{n}" for n in names)
    return (_doc(rng, f"Text and CSV presentation of {e.table}.") + f"\nimport csv\nimport io\n\nfrom tasker.models.{e.table} import {e.cls}\n\n" + _notes(rng) + f'''
COLUMNS = {tuple(names)!r}


def cells(item: {e.cls}) -> list[str]:
    """Display text of every column of one row."""
    return ["" if value is None else str(value) for value in ({cells},)]


def render_table(items: list[{e.cls}]) -> str:
    """Fixed width table with a header line."""
    rows   = [list(COLUMNS)] + [cells(item) for item in items]
    widths = [max(len(row[i]) for row in rows) for i in range(len(COLUMNS))]
    return "\\n".join("  ".join(text.ljust(width) for text, width in zip(row, widths, strict=True)).rstrip() for row in rows) + "\\n"


def export_csv(items: list[{e.cls}]) -> str:
    """CSV text with a header row."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\\n")
    writer.writerow(COLUMNS)
    for item in items:
        writer.writerow(cells(item))
    return buffer.getvalue()
''')


def openapi(es: list[Entity]) -> str:
    schemas, paths = {}, {}
    for e in es:
        props: dict[str, Any] = {"id": {"type": "integer", "readOnly": True}}
        for f in e.fields:
            prop: dict[str, Any] = {"type": "string" if f.kind == "str" else "integer"}
            if f.choices:
                prop["enum"] = list(f.choices)
            if f.low is not None:
                prop.update({"minimum": f.low, "maximum": f.high})
            if f.default is not None:
                prop["default"] = f.default
            props[f.name] = prop
        schemas[e.cls] = {"type": "object", "required": [f.name for f in e.fields if f.required and f.default is None], "properties": props}
        params = [{"name": f.name, "in": "query", "required": False, "schema": {"type": "string" if f.kind == "str" else "integer"}} for f in e.fields if f.filterable]
        ref = {"$ref": f"#/components/schemas/{e.cls}"}
        paths[f"/{e.table}"] = {"get": {"summary": f"List {e.table}", "parameters": params, "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "array", "items": ref}}}}}},
                                "post": {"summary": f"Create a {e.cls.lower()}", "requestBody": {"content": {"application/json": {"schema": ref}}}, "responses": {"201": {"description": "created"}, "422": {"description": "invalid"}}}}
        paths[f"/{e.table}/{{id}}"] = {"get": {"summary": f"Read a {e.cls.lower()}", "responses": {"200": {"description": "ok"}, "404": {"description": "missing"}}},
                                       "put": {"summary": f"Update a {e.cls.lower()}", "requestBody": {"content": {"application/json": {"schema": ref}}}, "responses": {"200": {"description": "ok"}, "422": {"description": "invalid"}}},
                                       "delete": {"summary": f"Delete a {e.cls.lower()}", "responses": {"200": {"description": "ok"}, "404": {"description": "missing"}}}}
    doc = {"openapi": "3.0.3", "info": {"title": "Tasker", "version": "1.0.0"}, "paths": paths, "components": {"schemas": schemas}}
    return json.dumps(doc, indent=2) + "\n"


STATIC = {
    "tasker/__init__.py": '"""Tasker: tickets, projects and people."""\n',
    "tasker/errors.py": '''"""Exceptions of the app."""


class ValidationError(Exception):
    """A record broke one or more rules."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class NotFound(Exception):
    """A record does not exist."""
''',
    "tasker/db.py": '''"""SQLite connection and the ordered migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def migration_files() -> list[Path]:
    """Migration scripts in the order they are applied."""
    return sorted(MIGRATIONS.glob("*.sql"))


def apply(conn: sqlite3.Connection, files: list[Path]) -> None:
    """Run the given scripts on a connection."""
    for file in files:
        conn.executescript(file.read_text(encoding="utf-8"))


def connect(path: str = ":memory:") -> sqlite3.Connection:
    """A connection with every migration applied."""
    conn = sqlite3.connect(path)
    apply(conn, migration_files())
    return conn
''',
}

README = """# Tasker

A small tickets app. Requests enter through tasker/api/router.py and travel down the layers:

1. migrations/*.sql: the schema, one script per change, applied in file name order by tasker/db.py
2. tasker/models: one dataclass per table
3. tasker/repository: SQL for one table
4. tasker/service: validation and workflows
5. tasker/api: request parsing and JSON shape; the contract is documented in openapi.json
6. tasker/view: text table and CSV rendering, used by tasker/cli.py

Existing migrations are never edited: a schema change is a new script.
"""

ROUTER = '''"""Route table of the app."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from tasker.api import __ENTITIES__

ROUTES = __ROUTES__
ITEM = re.compile(r"^/(?P<name>[a-z]+)/(?P<id>\\d+)$")
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
'''

CLI = '''"""Command line facade: text tables and CSV exports."""

from __future__ import annotations

import sqlite3

from tasker.service import __SERVICES__
from tasker.view import __VIEWS__

SERVICES = __SERVICE_MAP__
VIEWS    = __VIEW_MAP__


def show(conn: sqlite3.Connection, name: str) -> str:
    """Text table of every row of an entity."""
    return VIEWS[name].render_table(SERVICES[name].listing(conn, {}))


def export(conn: sqlite3.Connection, name: str) -> str:
    """CSV of every row of an entity."""
    return VIEWS[name].export_csv(SERVICES[name].listing(conn, {}))
'''

TEST_ENTITY = '''"""CRUD through the router for {table}."""

import unittest

from tasker import db
from tasker.api.router import dispatch


class {cls}Tests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect()

    def test_create_read_update_delete(self):
        status, created = dispatch(self.conn, "POST", "/{table}", body={sample!r})
        self.assertEqual(status, 201)
        item_id = created["id"]
        for key, value in {sample!r}.items():
            self.assertEqual(created[key], value)
        self.assertEqual(dispatch(self.conn, "GET", f"/{table}/{{item_id}}")[0], 200)
        status, updated = dispatch(self.conn, "PUT", f"/{table}/{{item_id}}", body={patch!r})
        self.assertEqual(status, 200)
        for key, value in {patch!r}.items():
            self.assertEqual(updated[key], value)
        self.assertEqual(len(dispatch(self.conn, "GET", "/{table}")[1]), 1)
        self.assertEqual(dispatch(self.conn, "DELETE", f"/{table}/{{item_id}}")[0], 200)
        self.assertEqual(dispatch(self.conn, "GET", f"/{table}/{{item_id}}")[0], 404)

    def test_unknown_field_is_rejected(self):
        status, payload = dispatch(self.conn, "POST", "/{table}", body={{**{sample!r}, "bogus": 1}})
        self.assertEqual(status, 422)
        self.assertIn("unknown field bogus", payload["errors"])


if __name__ == "__main__":
    unittest.main()
'''

CHECK = '''"""Checks the severity field of tickets through every layer. Exit status 0 means done."""

import csv
import io
import json
import pathlib
import sqlite3
import subprocess
import sys

from tasker import cli, db
from tasker.api.router import dispatch

problems = []


def expect(condition, message):
    if not condition:
        problems.append(message)


files = db.migration_files()
old = [f for f in files if "severity" not in f.read_text(encoding="utf-8").lower()]
new = [f for f in files if f not in old]
expect(new, "add a new migration script for the column; do not edit the existing ones")
legacy = sqlite3.connect(":memory:")
try:
    db.apply(legacy, old)
    legacy.execute("INSERT INTO tickets (project_id, title, status) VALUES (1, 'legacy', 'open')")
    db.apply(legacy, new)
    row = legacy.execute("SELECT severity FROM tickets WHERE title = 'legacy'").fetchone()
    expect(row is not None and row[0] == 3, "existing tickets must read as severity 3 after the migration")
except sqlite3.Error as exc:
    problems.append(f"migrating a database that holds old tickets failed: {exc}")

conn = db.connect()
dispatch(conn, "POST", "/projects", body={"name": "Apollo", "owner_id": 1})
status, plain = dispatch(conn, "POST", "/tickets", body={"project_id": 1, "title": "plain"})
expect(status == 201 and plain.get("severity") == 3, "a ticket without severity must default to 3")
status, high = dispatch(conn, "POST", "/tickets", body={"project_id": 1, "title": "urgent", "severity": 5})
expect(status == 201 and high.get("severity") == 5, "a ticket created with severity 5 must keep it")
status, again = dispatch(conn, "GET", f"/tickets/{high.get('id')}")
expect(status == 200 and again.get("severity") == 5, "reading a ticket must return its severity")
status, changed = dispatch(conn, "PUT", f"/tickets/{high.get('id')}", body={"severity": 2})
expect(status == 200 and changed.get("severity") == 2 and changed.get("title") == "urgent", "updating severity must change only severity")
for bad in (0, 6, -1, "high", 2.5, True):
    status, payload = dispatch(conn, "POST", "/tickets", body={"project_id": 1, "title": "bad", "severity": bad})
    expect(status == 422 and any("severity" in e for e in payload.get("errors", [])), f"severity {bad!r} must be rejected with a message naming severity")
dispatch(conn, "POST", "/tickets", body={"project_id": 1, "title": "second", "severity": 5})
status, listed = dispatch(conn, "GET", "/tickets", query={"severity": "5"})
expect(status == 200 and sorted(t["title"] for t in listed) == ["second"], "filtering tickets by severity must work")
status, opened = dispatch(conn, "GET", "/tickets", query={"status": "open"})
expect(status == 200 and len(opened) == 3, "filtering by status must keep working")
expect("severity" not in dispatch(conn, "GET", "/projects/1")[1], "only tickets get the field")

table = cli.show(conn, "tickets").splitlines()
expect("severity" in table[0].split(), "the ticket table needs a severity column")
rows = list(csv.reader(io.StringIO(cli.export(conn, "tickets"))))
expect(rows[0][-1] == "severity" and rows[0][:5] == ["id", "project_id", "title", "status", "assignee_id"], "the CSV header must end with severity")
expect([r[-1] for r in rows[1:]] == ["3", "2", "5"], "the CSV severity column must hold each ticket's severity")

spec = json.loads(pathlib.Path("openapi.json").read_text(encoding="utf-8"))
prop = spec["components"]["schemas"]["Ticket"]["properties"].get("severity", {})
expect(prop.get("type") == "integer" and prop.get("minimum") == 1 and prop.get("maximum") == 5, "openapi.json must describe severity as an integer from 1 to 5")
expect(any(p.get("name") == "severity" for p in spec["paths"]["/tickets"]["get"]["parameters"]), "openapi.json must list the severity filter")

tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], capture_output=True, text=True)
expect(tests.returncode == 0, "the unit tests must still pass: " + (tests.stderr.strip().splitlines() or ["no output"])[-1])
for problem in problems[:30]:
    print(problem)
if problems:
    print(f"{len(problems)} problem(s)")
    sys.exit(1)
print("ok")
'''


def render(with_severity: bool) -> dict[str, str]:
    """Every file of the app for one state."""
    rng   = rng_for(TASK_ID)
    es    = entities(with_severity)
    files = dict(STATIC)
    files["README.md"] = README
    files["openapi.json"] = openapi(es)
    for number, e in enumerate(es, 1):
        files[f"migrations/{number:04d}_{e.table}.sql"] = migration(e)
        files[f"tasker/models/{e.table}.py"]      = model(rng, e)
        files[f"tasker/repository/{e.table}.py"]  = repository(rng, e)
        files[f"tasker/service/{e.table}.py"]     = service(rng, e)
        files[f"tasker/api/{e.table}.py"]         = api(rng, e)
        files[f"tasker/view/{e.table}.py"]        = view(rng, e)
        files[f"tests/test_{e.table}.py"]         = TEST_ENTITY.format(table=e.table, cls=e.cls, sample=e.sample, patch=e.patch)
    files["tests/__init__.py"] = ""
    for layer in ("models", "repository", "service", "api", "view"):
        files[f"tasker/{layer}/__init__.py"] = ""
    names = [e.table for e in es]
    mapping = lambda prefix: "{" + ", ".join(f'"{n}": {prefix}{n}' for n in names) + "}"  # noqa: E731
    files["tasker/api/router.py"] = ROUTER.replace("__ENTITIES__", ", ".join(names)).replace("__ROUTES__", mapping(""))
    files["tasker/cli.py"] = (CLI.replace("__SERVICES__", ", ".join(names)).replace("__VIEWS__", ", ".join(f"{n} as view_{n}" for n in names))
                              .replace("__SERVICE_MAP__", mapping("")).replace("__VIEW_MAP__", mapping("view_")))
    return files


def build() -> TaskBuild:
    before = render(False)
    after  = render(True)
    after["migrations/0003_tickets.sql"] = before["migrations/0003_tickets.sql"]
    after["migrations/0007_ticket_severity.sql"] = "ALTER TABLE tickets ADD COLUMN severity INTEGER NOT NULL DEFAULT 3;\n"
    solution = {path: text for path, text in after.items() if before.get(path) != text}
    prompt = ("Add an integer field severity to tickets (and only tickets), from 1 to 5, default 3, through every layer of the app described in README.md: "
              "the schema (as a new migration; existing tickets must read as 3), the model, the repository, the service (validation with a message that names severity), "
              "the API (create, read, update, and a severity filter on the ticket list), the text table and CSV export (severity as the last column), and openapi.json. "
              "Keep the existing tests passing. `python3 check.py` must pass.")
    return TaskBuild(
        task_id  = TASK_ID,
        task     = {"prompt": prompt, "verify": "python3 check.py", "tags": ["python", "feature", "multi-file"]},
        fixture  = {**before, "check.py": CHECK},
        solution = solution,
        reading  = sorted(path for path in before if path.startswith(("tasker/", "migrations/", "README", "openapi"))),
    )
