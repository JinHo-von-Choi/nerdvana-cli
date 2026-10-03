"""Task long-data-consistency-repair: repair 30 data files so they agree with each other and with a spec.

Author: 최진호
Date:   2026-10-03

Customers, products and orders live in 30 JSON files. A clean dataset is generated first,
then corrupted in ways that each have exactly one correct repair, so the repaired files can
be compared with the clean ones by digest while the checker also reports rule violations.
"""

from __future__ import annotations

import copy
import json
import random
from datetime import date, timedelta
from typing import Any

from .common import TaskBuild, digest, fill, rng_for

TASK_ID    = "long-data-consistency-repair"
REGIONS    = ["EU", "NA", "APAC"]
CURRENCIES = ["USD", "EUR", "GBP"]
FIRST      = ["Ada", "Bram", "Chloe", "Dmitri", "Elena", "Farid", "Greta", "Hugo", "Ines", "Jonas", "Katya", "Liam", "Mira", "Noah", "Olga", "Pavel"]
LAST       = ["Anders", "Brandt", "Castro", "Dvorak", "Eriksen", "Fontaine", "Garcia", "Haruki", "Ivanov", "Jensen", "Kowalski", "Lindqvist"]
PRODUCTS   = ["bolt", "washer", "bracket", "hinge", "spring", "gasket", "valve", "nozzle", "bearing", "coupler", "clamp", "sensor"]
CUSTOMER_FILES, PRODUCT_FILES, ORDER_FILES = 5, 5, 20
CUSTOMERS_PER_FILE, PRODUCTS_PER_FILE, ORDERS_PER_FILE = 36, 24, 22

SPEC = """# Data rules

The files in data/ describe customers, products and orders. They have drifted apart. Repair them
so that every rule below holds. Do not add, delete or reorder records and do not change any value
that no rule asks you to change. A record's identity is its id (customers and orders) or its sku.

## Customers (customers_*.json)

1. id is `C-` followed by four digits.
2. region is one of EU, NA, APAC (upper case).
3. currency is one of USD, EUR, GBP (upper case).

The customer files are the source of truth for a customer's region and currency.

## Products (products_*.json)

4. sku is `SKU-` followed by four digits.
5. prices maps each of USD, EUR, GBP to an integer number of cents. The product files are the source of
   truth for prices.

## Orders (orders_*.json)

6. customer_id is the canonical id of an existing customer (`C-` and four digits). Some orders hold the id
   in another spelling (lower case, no dash, surrounding blanks, missing leading zeros); canonicalise it.
7. region equals the region of that customer.
8. currency equals the currency of that customer.
9. every item's sku is the canonical sku of an existing product (upper case, no blanks).
10. every item's unit_cents equals the product's price in the order's currency.
11. total_cents equals the sum of qty * unit_cents over the items.
12. placed is an ISO date `YYYY-MM-DD`. Some orders hold `DD/MM/YYYY`; convert them.

`python3 check.py` lists the violations it finds and compares the repaired files with the reference.
"""

CHECK = '''"""Checks the data repair. Exit status 0 means every rule holds and nothing else changed."""

import hashlib
import json
import pathlib
import re
import sys
from datetime import date

REFERENCE = __REFERENCE__
REGIONS, CURRENCIES = {"EU", "NA", "APAC"}, {"USD", "EUR", "GBP"}


def load(pattern):
    return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(pathlib.Path("data").glob(pattern))}


def digest(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def iso(text):
    try:
        return bool(re.fullmatch(r"\\d{4}-\\d{2}-\\d{2}", text)) and bool(date.fromisoformat(text))
    except ValueError:
        return False


problems = []
customers = {c["id"]: c for rows in load("customers_*.json").values() for c in rows}
products = {p["sku"]: p for rows in load("products_*.json").values() for p in rows}
for cid, c in customers.items():
    if not re.fullmatch(r"C-\\d{4}", cid) or c["region"] not in REGIONS or c["currency"] not in CURRENCIES:
        problems.append(f"customer {cid}: id, region or currency breaks rules 1-3")
for sku, p in products.items():
    if not re.fullmatch(r"SKU-\\d{4}", sku) or set(p["prices"]) != CURRENCIES or not all(isinstance(v, int) for v in p["prices"].values()):
        problems.append(f"product {sku}: sku or prices break rules 4-5")
for name, rows in load("orders_*.json").items():
    for o in rows:
        c = customers.get(o["customer_id"])
        if c is None:
            problems.append(f"{name} {o['id']}: customer_id {o['customer_id']!r} is not a canonical existing customer")
            continue
        if o["region"] != c["region"] or o["currency"] != c["currency"]:
            problems.append(f"{name} {o['id']}: region or currency differs from customer {c['id']}")
        for item in o["items"]:
            p = products.get(item["sku"])
            if p is None:
                problems.append(f"{name} {o['id']}: sku {item['sku']!r} is not a canonical existing product")
                continue
            if item["unit_cents"] != p["prices"].get(o["currency"]):
                problems.append(f"{name} {o['id']}: unit_cents of {item['sku']} differs from the catalog price")
        if o["total_cents"] != sum(i["qty"] * i["unit_cents"] for i in o["items"]):
            problems.append(f"{name} {o['id']}: total_cents is not the sum of the items")
        if not iso(o["placed"]):
            problems.append(f"{name} {o['id']}: placed {o['placed']!r} is not an ISO date")
if not problems:
    for name, expected in REFERENCE.items():
        path = pathlib.Path("data") / name
        if not path.is_file() or digest(json.loads(path.read_text(encoding="utf-8"))) != expected:
            problems.append(f"{name}: differs from the reference (a record was added, removed, reordered or changed beyond the rules)")
for problem in problems[:30]:
    print(problem)
if problems:
    print(f"{len(problems)} problem(s)")
    sys.exit(1)
print("ok")
'''


def _clean_dataset(rng: random.Random) -> dict[str, list[dict[str, Any]]]:
    files: dict[str, list[dict[str, Any]]] = {}
    customers: list[dict[str, Any]] = []
    for f in range(CUSTOMER_FILES):
        rows = []
        for k in range(CUSTOMERS_PER_FILE):
            n = f * CUSTOMERS_PER_FILE + k + 1
            rows.append({"id": f"C-{n:04d}", "name": f"{rng.choice(FIRST)} {rng.choice(LAST)}", "region": rng.choice(REGIONS),
                         "currency": rng.choice(CURRENCIES), "tier": rng.choice(["basic", "plus", "pro"])})
        files[f"customers_{f + 1:02d}.json"] = rows
        customers += rows
    products: list[dict[str, Any]] = []
    for f in range(PRODUCT_FILES):
        rows = []
        for k in range(PRODUCTS_PER_FILE):
            n = f * PRODUCTS_PER_FILE + k + 1
            base = rng.randrange(150, 9000)
            rows.append({"sku": f"SKU-{n:04d}", "title": f"{rng.choice(PRODUCTS)} {rng.randrange(2, 40)}mm",
                         "prices": {"USD": base, "EUR": base - base // 10, "GBP": base - base // 8}})
        files[f"products_{f + 1:02d}.json"] = rows
        products += rows
    start, number = date(2026, 1, 1), 0
    for f in range(ORDER_FILES):
        rows = []
        for _ in range(ORDERS_PER_FILE):
            number += 1
            cust  = rng.choice(customers)
            items = [{"sku": (p := rng.choice(products))["sku"], "qty": rng.randrange(1, 6), "unit_cents": p["prices"][cust["currency"]]}
                     for _ in range(rng.randrange(1, 4))]
            rows.append({"id": f"O-{number:05d}", "customer_id": cust["id"], "region": cust["region"], "currency": cust["currency"],
                         "placed": (start + timedelta(days=rng.randrange(0, 180))).isoformat(), "items": items,
                         "total_cents": sum(i["qty"] * i["unit_cents"] for i in items)})
        files[f"orders_{f + 1:02d}.json"] = rows
    return files


def _noncanonical_id(rng: random.Random, cid: str) -> str:
    n = int(cid[2:])
    return rng.choice([f"c-{n:04d}", f"C{n:04d}", f" {cid} ", f"C-{n}", f"c{n}"])


def _corrupt(rng: random.Random, clean: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    dirty = copy.deepcopy(clean)
    for name, rows in dirty.items():
        if name.startswith("customers_"):
            for row in rows:
                if rng.random() < 0.12:
                    row["region"] = row["region"].lower()
                if rng.random() < 0.12:
                    row["currency"] = row["currency"].lower()
        elif name.startswith("orders_"):
            for row in rows:
                _corrupt_order(rng, row, clean)
    return dirty


def _corrupt_order(rng: random.Random, row: dict[str, Any], clean: dict[str, list[dict[str, Any]]]) -> None:
    if rng.random() < 0.15:
        row["customer_id"] = _noncanonical_id(rng, row["customer_id"])
    if rng.random() < 0.10:
        row["region"] = rng.choice([r for r in REGIONS if r != row["region"]])
    if rng.random() < 0.10:
        row["currency"] = rng.choice([c for c in CURRENCIES if c != row["currency"]]) if rng.random() < 0.5 else row["currency"].lower()
    if rng.random() < 0.12:
        row["placed"] = date.fromisoformat(row["placed"]).strftime("%d/%m/%Y")
    for item in row["items"]:
        if rng.random() < 0.10:
            item["sku"] = rng.choice([item["sku"].lower(), item["sku"] + " ", " " + item["sku"]])
        if rng.random() < 0.10:
            item["unit_cents"] += rng.choice([-120, -30, 15, 99, 250])
    if rng.random() < 0.15:
        row["total_cents"] += rng.choice([-500, -1, 1, 70, 1234])


def _dump(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, indent=2) + "\n"


def build() -> TaskBuild:
    rng     = rng_for(TASK_ID)
    clean   = _clean_dataset(rng)
    dirty   = _corrupt(rng, clean)
    fixture = {f"data/{name}": _dump(rows) for name, rows in dirty.items()}
    solution = {f"data/{name}": _dump(rows) for name, rows in clean.items() if dirty[name] != rows}
    fixture["SPEC.md"]  = SPEC
    fixture["check.py"] = fill(CHECK, reference=repr({name: digest(rows) for name, rows in clean.items()}))
    prompt = ("The 30 JSON files in data/ (customers, products, orders) disagree with each other and with the rules in SPEC.md. "
              "Repair the data so that every rule holds, changing nothing else, then make `python3 check.py` pass.")
    return TaskBuild(
        task_id  = TASK_ID,
        task     = {"prompt": prompt, "verify": "python3 check.py", "tags": ["python", "data", "multi-file"]},
        fixture  = fixture,
        solution = solution,
        reading  = sorted(fixture),
    )
