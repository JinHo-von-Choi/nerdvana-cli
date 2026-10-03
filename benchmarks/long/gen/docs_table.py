"""Task long-docs-summary-table: read eight long project records and tabulate five facts from each.

Author: 최진호
Date:   2026-10-03

Every document states each fact once at kickoff and revises it later in dated sentences scattered
through the text, so the final value is the one with the latest date, not the one read last.
A risk register table is counted rather than read off. The checker embeds digests per cell.
"""

from __future__ import annotations

import csv
import io
import random
from dataclasses import dataclass
from datetime import date, timedelta

from .common import TaskBuild, fill, prose, rng_for, sha

TASK_ID   = "long-docs-summary-table"
CODENAMES = ["atlas", "borealis", "cinder", "delta", "ember", "fjord", "garnet", "harbor"]
PEOPLE    = ["Mara Lindqvist", "Tomas Brandt", "Priya Raman", "Joel Okafor", "Ines Duarte", "Kenji Arai", "Sofia Marchetti", "Dmitri Volkov",
             "Hana Kowalski", "Rafael Ortega", "Leila Haddad", "Anders Nyberg", "Chiara Bellini", "Yusuf Demir", "Elin Sorensen", "Marek Dvorak"]
VENDORS   = ["Northwind Systems", "Halcyon Cloud", "Ferrous Labs", "Quillon Data", "Brightmesh", "Tessera Works", "Corvid Networks", "Lumen Analytics",
             "Granite Compute", "Opaline Software", "Sable Logistics", "Verdant IT"]
SECTIONS  = ["Summary", "Governance", "Budget", "Milestones", "Vendor selection", "Architecture", "Testing strategy", "Rollout plan",
             "Training", "Compliance", "Operations"]
RISKS     = ["supplier delivery slips past the freeze", "data migration reveals duplicate records", "key reviewer is unavailable during cutover",
             "load test environment differs from production", "licence renewal lands inside the release window", "interface contract changes late",
             "audit finding reopens an approved design", "training material lags behind the build", "monitoring gaps hide a failing batch",
             "network change window is shortened", "rollback script is untested on the latest schema", "capacity estimate ignores month end peaks"]
STATUSES  = ["Open", "Closed", "Mitigated", "Accepted", "Transferred"]
COLUMNS   = ["doc", "owner", "final_budget_usd", "go_live", "vendor", "open_risks"]

CHECK = '''"""Checks summary.csv. Exit status 0 means every cell is right."""

import csv
import hashlib
import pathlib
import sys

EXPECTED = __EXPECTED__
COLUMNS = __COLUMNS__


def digest(text):
    return hashlib.sha256(text.strip().encode()).hexdigest()[:16]


path = pathlib.Path("summary.csv")
if not path.is_file():
    print("summary.csv is missing")
    sys.exit(1)
with path.open(encoding="utf-8", newline="") as handle:
    reader = csv.DictReader(handle)
    header = reader.fieldnames
    rows = {row["doc"].strip(): row for row in reader if row.get("doc")}
problems = []
if header != COLUMNS:
    problems.append(f"header must be {','.join(COLUMNS)}")
for doc, cells in EXPECTED.items():
    row = rows.get(doc)
    if row is None:
        problems.append(f"row for {doc} is missing")
        continue
    for column, expected in cells.items():
        if digest(row.get(column) or "") != expected:
            problems.append(f"{doc}: {column} is wrong")
for doc in sorted(set(rows) - set(EXPECTED)):
    problems.append(f"{doc}: not a document")
for problem in problems[:40]:
    print(problem)
if problems:
    print(f"{len(problems)} problem(s)")
    sys.exit(1)
print("ok")
'''

PROMPT = (
    "docs/ holds eight project records, each long and each revised over time. Write summary.csv with the header "
    "doc,owner,final_budget_usd,go_live,vendor,open_risks and one row per document (doc is the file name without .md). "
    "owner is the person who owns the project according to the latest dated decision; final_budget_usd is the project budget in force after the latest "
    "dated budget decision, as digits only (ignore pilot, training and contingency figures); go_live is the latest dated go-live date as YYYY-MM-DD; "
    "vendor is the contracted vendor according to the latest dated decision; open_risks is the number of rows of the risk register table whose status "
    "is exactly Open. When a document revises a value, the sentence with the latest date wins wherever it appears. Then make `python3 check.py` pass."
)


@dataclass
class Event:
    """A dated statement about one fact."""

    when:  date
    value: str
    text:  str


def _fmt_money(amount: int) -> str:
    return f"${amount:,}"


def _events(rng: random.Random, people: list[str]) -> tuple[dict[str, list[Event]], dict[str, str]]:
    """Dated events per fact (first one is the kickoff statement) and the final value of each fact."""
    base = date(2026, 1, 12)
    facts: dict[str, list[Event]] = {}
    owners = rng.sample(people, 3)
    budgets = [rng.randrange(80, 400) * 1000]
    vendors = rng.sample(VENDORS, 3)
    golives = [date(2026, rng.randrange(8, 10), rng.randrange(1, 28))]
    for _ in range(2):
        budgets.append(budgets[-1] + rng.choice([-1, 1]) * rng.randrange(5, 60) * 500)
        golives.append(golives[-1] + timedelta(days=rng.randrange(10, 70) * rng.choice([1, 1, -1])))
    stamps = sorted(rng.sample(range(30, 170), 8))
    dates  = [base + timedelta(days=d) for d in stamps]
    rng.shuffle(dates)

    def revisions(count: int) -> list[date]:
        return sorted(dates.pop() for _ in range(count))

    o = revisions(rng.randrange(1, 3))
    facts["owner"] = [Event(base, owners[0], f"Project owner at kickoff ({base.isoformat()}): {owners[0]}.")] + [
        Event(when, owners[i + 1], f"Effective {when.isoformat()}, ownership of the project passes to {owners[i + 1]}.") for i, when in enumerate(o)]
    b = revisions(rng.randrange(1, 3))
    facts["budget"] = [Event(base, str(budgets[0]), f"Baseline budget approved on {base.isoformat()}: {_fmt_money(budgets[0])}.")] + [
        Event(when, str(budgets[i + 1]), f"On {when.isoformat()} the steering group set the project budget to {_fmt_money(budgets[i + 1])}.") for i, when in enumerate(b)]
    g = revisions(rng.randrange(1, 3))
    facts["go_live"] = [Event(base, golives[0].isoformat(), f"Original go-live target (set {base.isoformat()}): {golives[0].isoformat()}.")] + [
        Event(when, golives[i + 1].isoformat(), f"As of {when.isoformat()}, go-live is scheduled for {golives[i + 1].isoformat()}.") for i, when in enumerate(g)]
    v = revisions(1) if dates else []
    facts["vendor"] = [Event(base, vendors[0], f"Vendor selected at kickoff ({base.isoformat()}): {vendors[0]}.")] + [
        Event(when, vendors[1], f"Following the review of {when.isoformat()}, {vendors[1]} is the contracted vendor.") for when in v]
    final = {fact: max(events, key=lambda e: e.when).value for fact, events in facts.items()}
    return facts, final


def _risk_table(rng: random.Random) -> tuple[str, int]:
    rows = ["| ID | Risk | Owner | Status |", "|-|-|-|-|"]
    open_count = 0
    for n in range(1, rng.randrange(38, 46)):
        status = rng.choices(STATUSES, [3, 3, 2, 1, 1])[0]
        open_count += status == "Open"
        rows.append(f"| R-{n:03d} | {rng.choice(RISKS).capitalize()} ({rng.choice(['phase 1', 'phase 2', 'cutover', 'hypercare'])}) | {rng.choice(PEOPLE).split()[0]} | {status} |")
    return "\n".join(rows), open_count


def _decoys(rng: random.Random) -> dict[str, str]:
    return {
        "Budget": f"The pilot phase ran on its own budget of {_fmt_money(rng.randrange(8, 20) * 1000)}. A contingency reserve of {_fmt_money(rng.randrange(3, 9) * 1000)} is held outside the project budget, and the training budget of {_fmt_money(rng.randrange(5, 12) * 1000)} is tracked separately.",
        "Milestones": f"The pilot went live on {date(2026, 2, rng.randrange(1, 28)).isoformat()} for a single region. That date is not the go-live of the project.",
        "Governance": f"The review chair is {rng.choice(PEOPLE)}, who does not own the project.",
        "Vendor selection": f"Also evaluated and not selected at the time: {', '.join(rng.sample(VENDORS, 2))}.",
    }


def _document(rng: random.Random, code: str, people: list[str]) -> tuple[str, dict[str, str]]:
    facts, final = _events(rng, people)
    decoys       = _decoys(rng)
    table, opened = _risk_table(rng)
    final["open_risks"] = str(opened)
    sections: dict[str, list[str]] = {name: [prose(rng, rng.randrange(6, 9)) for _ in range(4)] for name in SECTIONS}
    anchors = {"owner": "Governance", "budget": "Budget", "go_live": "Milestones", "vendor": "Vendor selection"}
    for fact, events in facts.items():
        sections[anchors[fact]].insert(0, events[0].text)
    for name, text in decoys.items():
        sections[name].insert(1, text)
    later = [e for events in facts.values() for e in events[1:]]
    appendix = [e for e in later if rng.random() < 0.4]
    for event in later:
        if event not in appendix:
            target = rng.choice(SECTIONS[5:])
            sections[target].insert(rng.randrange(len(sections[target]) + 1), event.text)
    lines = [f"# Project {code.capitalize()}: delivery record", ""]
    for number, name in enumerate(SECTIONS, 1):
        lines += [f"## {number}. {name}", ""] + [p + "\n" for p in sections[name]]
    lines += [f"## {len(SECTIONS) + 1}. Risk register", "", prose(rng, 4), "", table, "", f"## {len(SECTIONS) + 2}. Appendix A: change log", ""]
    lines += [f"- {e.text}" for e in sorted(appendix, key=lambda e: rng.random())] or ["- No changes were recorded in this log."]
    return "\n".join(lines) + "\n", final


def build() -> TaskBuild:
    rng     = rng_for(TASK_ID)
    docs    = {}
    answers = {}
    for code in CODENAMES:
        text, final = _document(rng, code, PEOPLE)
        docs[f"docs/project-{code}.md"] = text
        answers[f"project-{code}"] = final
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for doc, final in answers.items():
        writer.writerow([doc, final["owner"], final["budget"], final["go_live"], final["vendor"], final["open_risks"]])
    keys     = ("owner", "budget", "go_live", "vendor", "open_risks")
    expected = {doc: dict(zip(COLUMNS[1:], (sha(final[k])[:16] for k in keys), strict=True)) for doc, final in answers.items()}
    fixture = {**docs, "check.py": fill(CHECK, expected=repr(expected), columns=repr(COLUMNS))}
    return TaskBuild(
        task_id  = TASK_ID,
        task     = {"prompt": PROMPT, "verify": "python3 check.py", "tags": ["python", "reading", "analysis"]},
        fixture  = fixture,
        solution = {"summary.csv": buffer.getvalue()},
        reading  = sorted(docs),
    )
