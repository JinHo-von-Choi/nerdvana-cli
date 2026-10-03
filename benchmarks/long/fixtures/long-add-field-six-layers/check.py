"""Checks the severity field of tickets through every layer. Exit status 0 means done."""

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
