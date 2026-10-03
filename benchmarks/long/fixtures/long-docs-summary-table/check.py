"""Checks summary.csv. Exit status 0 means every cell is right."""

import csv
import hashlib
import pathlib
import sys

EXPECTED = {'project-atlas': {'owner': 'a1ea72ac3a96083d', 'final_budget_usd': '8e374748c94e0df8', 'go_live': '8e73856aa74988c0', 'vendor': '2a43736ac0a851c6', 'open_risks': 'b17ef6d19c7a5b1e'}, 'project-borealis': {'owner': 'f161d3d82d56ad12', 'final_budget_usd': 'f360264ca65d67d4', 'go_live': '43db3cb95c2941e2', 'vendor': '8de944487003df78', 'open_risks': '6b51d431df5d7f14'}, 'project-cinder': {'owner': 'c97f4859d8aa77c8', 'final_budget_usd': '022b8557c03033bf', 'go_live': '9b723f4d91897e1a', 'vendor': '3aeb2858ada78bf7', 'open_risks': '4a44dc15364204a8'}, 'project-delta': {'owner': '00f0e437f0755cd2', 'final_budget_usd': 'a700848d8c746ef4', 'go_live': 'bc0d014812921636', 'vendor': '893da1d68cc07797', 'open_risks': 'e629fa6598d73276'}, 'project-ember': {'owner': '508eb8582f290f49', 'final_budget_usd': '4f9f73b34c5b8987', 'go_live': 'df8e31284b727053', 'vendor': '818392ef16243227', 'open_risks': 'e7f6c011776e8db7'}, 'project-fjord': {'owner': '00f0e437f0755cd2', 'final_budget_usd': '117b28480e070935', 'go_live': '03fbce00745f7e7e', 'vendor': '5dc1d5679b544bd8', 'open_risks': '4a44dc15364204a8'}, 'project-garnet': {'owner': '0be225b273dc24b3', 'final_budget_usd': '858811f04a9d807e', 'go_live': '8cf7e84e772af381', 'vendor': '057cdca1a887ca63', 'open_risks': 'e629fa6598d73276'}, 'project-harbor': {'owner': '00f0e437f0755cd2', 'final_budget_usd': 'fe7f9e59b585a720', 'go_live': 'e4bc2ddfd7fb7c8f', 'vendor': '8de944487003df78', 'open_risks': '19581e27de7ced00'}}
COLUMNS = ['doc', 'owner', 'final_budget_usd', 'go_live', 'vendor', 'open_risks']


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
