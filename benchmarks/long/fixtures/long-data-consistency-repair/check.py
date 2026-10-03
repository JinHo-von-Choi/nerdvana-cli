"""Checks the data repair. Exit status 0 means every rule holds and nothing else changed."""

import hashlib
import json
import pathlib
import re
import sys
from datetime import date

REFERENCE = {'customers_01.json': '63f918e07a9cd42d', 'customers_02.json': '7c96ab2aebaae5e7', 'customers_03.json': '433ba63ea02e5c3c', 'customers_04.json': '6b893ad5c41f9340', 'customers_05.json': '62e1652745cc360b', 'products_01.json': 'cf23f999ce33974e', 'products_02.json': '0923b29322fe60f4', 'products_03.json': '45344b941d3d2933', 'products_04.json': 'ce9ac5b39095745d', 'products_05.json': 'a730fd59699eaba3', 'orders_01.json': 'ac6d09e24a9704ea', 'orders_02.json': 'f3821c3794b539d4', 'orders_03.json': '3d52b8d3fcde5947', 'orders_04.json': '22bdbf51f12b9c2d', 'orders_05.json': '0ccdc21fedfd9ad7', 'orders_06.json': '54ede51ae023f6bb', 'orders_07.json': '1f71fb91ea8ee483', 'orders_08.json': '5c75920ba6f54382', 'orders_09.json': '067f8278070a2a83', 'orders_10.json': 'fcf7a7916e14afe6', 'orders_11.json': 'd3c093eddc990bb3', 'orders_12.json': '48130b6a7540bf87', 'orders_13.json': 'b084b9b4966241a5', 'orders_14.json': '8f2c58e7c19f659f', 'orders_15.json': '7a15c43f5dacfdc5', 'orders_16.json': '0b43907f581b4281', 'orders_17.json': 'ab13d67009c9cbe5', 'orders_18.json': '99a884bb6f41a105', 'orders_19.json': 'b124d07d4e797012', 'orders_20.json': 'e3d78fdb5462c5b4'}
REGIONS, CURRENCIES = {"EU", "NA", "APAC"}, {"USD", "EUR", "GBP"}


def load(pattern):
    return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(pathlib.Path("data").glob(pattern))}


def digest(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def iso(text):
    try:
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", text)) and bool(date.fromisoformat(text))
    except ValueError:
        return False


problems = []
customers = {c["id"]: c for rows in load("customers_*.json").values() for c in rows}
products = {p["sku"]: p for rows in load("products_*.json").values() for p in rows}
for cid, c in customers.items():
    if not re.fullmatch(r"C-\d{4}", cid) or c["region"] not in REGIONS or c["currency"] not in CURRENCIES:
        problems.append(f"customer {cid}: id, region or currency breaks rules 1-3")
for sku, p in products.items():
    if not re.fullmatch(r"SKU-\d{4}", sku) or set(p["prices"]) != CURRENCIES or not all(isinstance(v, int) for v in p["prices"].values()):
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
