"""Task long-move-function: move three names out of a helper module and update 20 importers.

Author: 최진호
Date:   2026-10-03

The importers use six import styles: from-import (with and without other names), module
attribute, aliased module, relative import, function-local import and a relative sibling import.
Twenty distractor modules import other names from the same helper module and must stay as they are.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .common import TaskBuild, fill, prose, rng_for, run_python

TASK_ID = "long-move-function"
MOVED   = ["format_money", "parse_money", "CURRENCY_SYMBOLS"]

# (package, module, style): 1 from-import, 2 module attribute, 3 aliased module, 4 relative, 5 function-local, 6 relative sibling
IMPORTERS = [
    ("billing", "invoice", 2), ("billing", "receipt", 1), ("billing", "statement", 4), ("billing", "credit_note", 1), ("billing", "tax_report", 5),
    ("reports", "daily", 1), ("reports", "monthly", 2), ("reports", "aging", 3), ("reports", "forecast", 4), ("reports", "summary", 5),
    ("export", "csv_export", 1), ("export", "html_export", 3), ("export", "json_export", 2),
    ("api", "handlers", 1), ("api", "serializers", 4), ("api", "views", 5),
    ("cli", "commands", 1), ("cli", "console", 2), ("cli", "formatters", 3),
    ("legacy", "report_utils", 6),
]
DISTRACTORS = [("core", n) for n in ("config", "clock", "ids", "paths", "retry", "events", "limits", "queues", "tokens", "schema")] + \
              [("util", n) for n in ("text", "numbers", "tables", "dates", "ranges", "labels", "sorting", "windows", "slugs", "counters")] + \
              [("ops", n) for n in ("deploys", "alerts", "rotation", "capacity", "audit", "backups", "quotas", "leases", "probes", "drains")]

HELPERS_HEAD = '''"""General purpose helpers shared by the acme packages."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
'''

MONEY_BLOCK = '''

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "EUR ", "GBP": "GBP ", "JPY": "JPY "}


def format_money(cents: int, currency: str = "USD") -> str:
    """Format an amount in minor units, for example 123456 USD as $1,234.56."""
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}{CURRENCY_SYMBOLS.get(currency, currency + ' ')}{whole:,}.{fraction:02d}"


def parse_money(text: str) -> int:
    """Read an amount such as $1,234.56 or -EUR 7.5 back into minor units."""
    cleaned = re.sub(r"[^0-9.\\-]", "", text)
    return int((Decimal(cleaned) * 100).to_integral_value(rounding=ROUND_HALF_UP))
'''

HELPERS_TAIL = '''

def slugify(text: str) -> str:
    """Lower case text joined by single dashes."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def clamp(value: int, low: int, high: int) -> int:
    """Keep a value inside an inclusive range."""
    return max(low, min(high, value))


def chunked(items: list[int], size: int) -> list[list[int]]:
    """Split a list into consecutive chunks of at most the given size."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def percent(part: int, whole: int) -> str:
    """A share as a percentage with one decimal, rounded half up."""
    if whole == 0:
        return "0.0%"
    value = (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{value}%"


def truncate_words(text: str, limit: int) -> str:
    """Cut text to the given number of words, adding an ellipsis when something was dropped."""
    words = text.split()
    return " ".join(words[:limit]) + (" ..." if len(words) > limit else "")


def pluralize(count: int, noun: str) -> str:
    """Count with a naive plural."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def parse_bool(text: str) -> bool:
    """Interpret common yes and no spellings."""
    return text.strip().lower() in {"1", "true", "yes", "on"}


def pad_left(text: str, width: int, fill: str = " ") -> str:
    """Right align text in a field."""
    return text.rjust(width, fill)


def flatten(rows: list[list[int]]) -> list[int]:
    """One list from a list of lists."""
    return [value for row in rows for value in row]


def median(values: list[int]) -> int:
    """Middle value of a non-empty list, the lower one for even lengths."""
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def roman(number: int) -> str:
    """Roman numeral for 1 to 3999."""
    out = ""
    for value, glyph in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while number >= value:
            out += glyph
            number -= value
    return out


def initials(name: str) -> str:
    """First letters of each word, upper case."""
    return "".join(word[0].upper() for word in name.split())


def describe_price(cents: int, currency: str = "USD") -> str:
    """A price with its currency spelled out for plain text channels."""
    return f"{__FORMAT__(cents, currency)} ({currency})"
'''


@dataclass
class Importer:
    """One module that uses the moved names."""

    package: str
    name:    str
    style:   int
    used:    list[str]
    slug:    bool
    factor:  int
    doc:     str
    notes:   list[str]


def helpers_text(after: bool) -> str:
    """The helper module before or after the move."""
    tail = HELPERS_TAIL.replace("__FORMAT__", "formatting.format_money" if after else "format_money")
    head = HELPERS_HEAD + ("\nfrom acme.money import formatting\n" if after else "")
    return head + ("" if after else MONEY_BLOCK) + tail


def money_text() -> str:
    """The new module that receives the moved names."""
    head = '"""Money formatting and parsing."""\n\nfrom __future__ import annotations\n\nimport re\nfrom decimal import ROUND_HALF_UP, Decimal\n'
    return head + MONEY_BLOCK


def _specs(rng: random.Random) -> list[Importer]:
    specs = []
    for package, name, style in IMPORTERS:
        used = [n for n in MOVED if rng.random() < 0.6] or [MOVED[rng.randrange(3)]]
        if name == "invoice":
            used = ["format_money", "parse_money"]
        slug = style in (2, 3) or rng.random() < 0.5
        specs.append(Importer(package, name, style, used, slug, rng.randrange(2, 9), prose(rng, 4), [prose(rng, 5) for _ in range(16)]))
    return specs


def _refs(spec: Importer, after: bool) -> dict[str, str]:
    """How each name is written inside a function of this module."""
    mod = {2: "formatting" if after else "helpers", 3: "fmt" if after else "h"}.get(spec.style)
    refs = {n: (f"{mod}.{n}" if mod else n) for n in MOVED}
    refs["slugify"] = {2: "helpers.slugify", 3: "h.slugify"}.get(spec.style, "slugify")
    return refs


def _import_lines(spec: Importer, after: bool) -> list[str]:
    used, slug = spec.used, spec.slug
    names = ", ".join(used)
    if spec.style == 1:
        if not after:
            return [f"from acme.legacy.helpers import {', '.join(used + (['slugify'] if slug else []))}"]
        return ([ "from acme.legacy.helpers import slugify"] if slug else []) + [f"from acme.money.formatting import {names}"]
    if spec.style == 2:
        return ["from acme.legacy import helpers"] + (["from acme.money import formatting"] if after else [])
    if spec.style == 3:
        return ["import acme.legacy.helpers as h"] + (["import acme.money.formatting as fmt"] if after else [])
    if spec.style in (4, 6):
        base, money = ("..legacy.helpers", "..money.formatting") if spec.style == 4 else (".helpers", "..money.formatting")
        if not after:
            return [f"from {base} import {', '.join(used + (['slugify'] if slug else []))}"]
        return ([f"from {base} import slugify"] if slug else []) + [f"from {money} import {names}"]
    return ["from acme.legacy.helpers import slugify"] if slug else []


def _local_import(spec: Importer, name: str, after: bool) -> str:
    if spec.style != 5:
        return ""
    return f"    from {'acme.money.formatting' if after else 'acme.legacy.helpers'} import {name}\n"


def _functions(spec: Importer, after: bool) -> tuple[list[str], list[str]]:
    refs, label = _refs(spec, after), f"{spec.package}.{spec.name}"
    funcs, calls = [], []
    if "format_money" in spec.used:
        local = _local_import(spec, "format_money", after)
        body  = '    return f"' + label + ": {" + refs["format_money"] + "(cents, 'USD')}" + '"\n'
        funcs.append('def amount_label(cents: int) -> str:\n    """Label an amount of this module."""\n' + local + body)
        calls.append(f"amount_label({12345 + spec.factor})")
    if "parse_money" in spec.used:
        local = _local_import(spec, "parse_money", after)
        funcs.append('def parse_total(text: str) -> int:\n    """Total in minor units scaled by the module factor."""\n'
                     + local + f"    return {refs['parse_money']}(text) * {spec.factor}\n")
        calls.append('parse_total("$1,234.56")')
    if "CURRENCY_SYMBOLS" in spec.used:
        local = _local_import(spec, "CURRENCY_SYMBOLS", after)
        funcs.append('def symbol_for(code: str) -> str:\n    """Symbol shown in front of amounts in a currency."""\n'
                     + local + f'    return {refs["CURRENCY_SYMBOLS"]}.get(code, "?")\n')
        calls.append('symbol_for("EUR")')
    if spec.slug:
        funcs.append('def tag(text: str) -> str:\n    """Slug used to tag entries of this module."""\n' + f"    return {refs['slugify']}(text)\n")
        calls.append(f'tag("Entry {spec.factor} of {spec.name}")')
    return funcs, calls


def render_importer(spec: Importer, after: bool) -> str:
    """Source text of one importer module."""
    funcs, calls = _functions(spec, after)
    lines = [f'"""{spec.package} {spec.name}.\n\n{spec.doc}\n"""', "", "from __future__ import annotations", ""]
    imports = _import_lines(spec, after)
    lines += imports + ([""] if imports else [])
    lines += [f'NOTE_{k + 1} = (\n    "{note}"\n)' for k, note in enumerate(spec.notes)] + ["", "", ""]
    demo = "def demo() -> str:\n    \"\"\"Evaluate every function of the module once.\"\"\"\n    return \" | \".join([\n" + "".join(f"        str({c}),\n" for c in calls) + "    ])\n"
    return "\n".join(lines) + "\n\n".join(funcs + [demo])


def render_distractor(rng: random.Random, package: str, name: str) -> str:
    """A module that imports only unmoved names from the helper module."""
    names = sorted(rng.sample(["slugify", "clamp", "percent", "pluralize", "median", "initials", "roman"], 3))
    calls = {"slugify": 'slugify("Some Title 7")', "clamp": "clamp(120, 0, 100)", "percent": "percent(3, 8)", "pluralize": 'pluralize(3, "item")',
             "median": "median([5, 1, 9, 3])", "initials": 'initials("Grace Brewster Hopper")', "roman": "roman(1994)"}
    note = ""
    if rng.random() < 0.3:
        note = "\n# Amounts are formatted by the caller (see format_money in the helper module).\n"
    body = "".join(f"        str({calls[n]}),\n" for n in names)
    return (f'"""{package} {name}.\n\n{prose(rng, 4)}\n"""\n\nfrom __future__ import annotations\n\nfrom acme.legacy.helpers import {", ".join(names)}\n{note}\n'
            + "".join(f'NOTE_{k + 1} = (\n    "{prose(rng, 3)}"\n)\n' for k in range(12))
            + f'\n\ndef demo() -> str:\n    """Evaluate the helpers this module relies on."""\n    return " | ".join([\n{body}    ])\n')


COMPACT = '''"""Compact money text for narrow tables."""

from __future__ import annotations


def format_money_compact(cents: int) -> str:
    """Whole units only, for example 1235 for 123456 cents."""
    return str(round(cents / 100))


def demo() -> str:
    return format_money_compact(123456)
'''

APP = '''"""Run the demo of every module and join the results."""

from __future__ import annotations

import importlib
import pkgutil

import acme


def modules() -> list[str]:
    """Names of all acme modules that define demo(), in sorted order."""
    found = []
    for info in pkgutil.walk_packages(acme.__path__, "acme."):
        if not info.ispkg and hasattr(importlib.import_module(info.name), "demo"):
            found.append(info.name)
    return sorted(found)


def run_all() -> str:
    """One text with the demo output of every module."""
    return "\\n".join(f"{name}: {importlib.import_module(name).demo()}" for name in modules())


if __name__ == "__main__":
    print(run_all())
'''

TEST_MONEY = '''"""Money formatting and parsing."""

import unittest

from __FORMATTING__ import CURRENCY_SYMBOLS, format_money, parse_money


class MoneyTests(unittest.TestCase):
    def test_format(self):
        self.assertEqual(format_money(123456), "$1,234.56")
        self.assertEqual(format_money(-5, "EUR"), "-EUR 0.05")

    def test_parse(self):
        self.assertEqual(parse_money("$1,234.56"), 123456)
        self.assertEqual(parse_money("-EUR 7.5"), -750)

    def test_symbols(self):
        self.assertEqual(CURRENCY_SYMBOLS["USD"], "$")


if __name__ == "__main__":
    unittest.main()
'''

TEST_INVOICE = '''"""Invoice labels."""

import unittest
from unittest import mock

from acme.billing import invoice


class InvoiceTests(unittest.TestCase):
    def test_label_uses_the_money_formatter(self):
        with mock.patch("__TARGET__", return_value="FMT"):
            self.assertEqual(invoice.amount_label(5), "billing.invoice: FMT")

    def test_label_text(self):
        self.assertEqual(invoice.amount_label(5), "billing.invoice: $0.05")


if __name__ == "__main__":
    unittest.main()
'''

TEST_HELPERS = '''"""Helpers that are not about money."""

import unittest

from acme.legacy import helpers


class HelperTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(helpers.slugify("Hello, World"), "hello-world")

    def test_describe_price(self):
        self.assertEqual(helpers.describe_price(250, "EUR"), "EUR 2.50 (EUR)")

    def test_percent(self):
        self.assertEqual(helpers.percent(1, 8), "12.5%")


if __name__ == "__main__":
    unittest.main()
'''

CHECK = '''"""Checks the move of the money helpers. Exit status 0 means done."""

import ast
import hashlib
import os
import pathlib
import subprocess
import sys

MOVED = {"format_money", "parse_money", "CURRENCY_SYMBOLS"}
HELPERS = "acme.legacy.helpers"
OUTPUT_DIGEST = "__DIGEST__"
problems = []


def top_names(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name.split(".")[0]) for a in node.names)
    return names


def absolute(path, node):
    if node.level == 0:
        return node.module or ""
    parts = list(path.with_suffix("").parts)[:-1]
    base = parts[: len(parts) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def scan(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = absolute(path, node)
            if module == HELPERS and MOVED & {a.name for a in node.names}:
                problems.append(f"{path}:{node.lineno}: imports a moved name from {HELPERS}")
            if module == "acme.legacy":
                bound.update(a.asname or a.name for a in node.names if a.name == "helpers")
        elif isinstance(node, ast.Import):
            bound.update(a.asname for a in node.names if a.name == HELPERS and a.asname)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in MOVED and isinstance(node.value, ast.Name) and node.value.id in bound:
            problems.append(f"{path}:{node.lineno}: {node.value.id}.{node.attr} still goes through the helper module")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and any(f"{HELPERS}.{n}" in node.value for n in MOVED):
            problems.append(f"{path}:{node.lineno}: string refers to {node.value}")


helpers = pathlib.Path("acme/legacy/helpers.py")
if MOVED & top_names(ast.parse(helpers.read_text(encoding="utf-8"))):
    problems.append("acme/legacy/helpers.py still defines or imports a moved name")
money = pathlib.Path("acme/money/formatting.py")
if not money.is_file() or not MOVED <= top_names(ast.parse(money.read_text(encoding="utf-8"))):
    problems.append("acme/money/formatting.py must define format_money, parse_money and CURRENCY_SYMBOLS")
for path in sorted(list(pathlib.Path("acme").rglob("*.py")) + list(pathlib.Path("tests").rglob("*.py"))):
    scan(path)
env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
if not problems:
    run = subprocess.run([sys.executable, "-c", "import hashlib, acme.app as a; print(hashlib.sha256(a.run_all().encode()).hexdigest()[:16])"], capture_output=True, text=True, env=env)
    if run.returncode != 0:
        problems.append("running every module failed: " + (run.stderr.strip().splitlines() or ["no output"])[-1])
    elif run.stdout.strip() != OUTPUT_DIGEST:
        problems.append("the modules print different output than before the move")
    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], capture_output=True, text=True, env=env)
    if tests.returncode != 0:
        problems.append("unit tests fail: " + (tests.stderr.strip().splitlines() or ["no output"])[-1])
for problem in problems[:30]:
    print(problem)
if problems:
    print(f"{len(problems)} problem(s)")
    sys.exit(1)
print("ok")
'''


def build() -> TaskBuild:
    rng       = rng_for(TASK_ID)
    specs     = _specs(rng)
    fixture   = {"acme/__init__.py": '"""acme application."""\n', "acme/app.py": APP, "tests/__init__.py": ""}
    for package in sorted({p for p, *_ in IMPORTERS} | {p for p, _ in DISTRACTORS}):
        fixture[f"acme/{package}/__init__.py"] = f'"""{package} package."""\n'
    fixture["acme/legacy/helpers.py"] = helpers_text(False)
    solution  = {"acme/legacy/helpers.py": helpers_text(True), "acme/money/__init__.py": '"""Money helpers."""\n', "acme/money/formatting.py": money_text()}
    for spec in specs:
        path = f"acme/{spec.package}/{spec.name}.py"
        fixture[path] = render_importer(spec, False)
        after = render_importer(spec, True)
        solution[path] = after
    for package, name in DISTRACTORS:
        fixture[f"acme/{package}/{name}.py"] = render_distractor(rng, package, name)
    fixture["acme/export/compact.py"] = COMPACT
    fixture["tests/test_money.py"]    = fill(TEST_MONEY, formatting="acme.legacy.helpers")
    fixture["tests/test_invoice.py"]  = fill(TEST_INVOICE, target="acme.legacy.helpers.format_money")
    fixture["tests/test_helpers.py"]  = TEST_HELPERS
    solution["tests/test_money.py"]   = fill(TEST_MONEY, formatting="acme.money.formatting")
    solution["tests/test_invoice.py"] = fill(TEST_INVOICE, target="acme.money.formatting.format_money")
    code   = "import hashlib, acme.app as a; print(hashlib.sha256(a.run_all().encode()).hexdigest()[:16])"
    output = run_python(fixture, code).strip()
    merged = {**fixture, **solution}
    assert run_python(merged, code).strip() == output, "the move must not change the output"
    fixture["check.py"] = fill(CHECK, digest=output)
    prompt = ("format_money, parse_money and CURRENCY_SYMBOLS are defined in acme/legacy/helpers.py next to a dozen unrelated helpers. "
              "Move those three names into a new module acme/money/formatting.py (a new package acme.money) and update everything that uses them: "
              "about twenty modules in several packages import them in different ways (plain, aliased, relative, inside functions), and the tests refer to them too. "
              "After the move they must not be reachable through acme.legacy.helpers any more, and the helper module's own functions that need them must keep working. "
              "Behaviour must not change. Do not touch modules that only use the other helpers. `python3 check.py` must pass.")
    return TaskBuild(
        task_id  = TASK_ID,
        task     = {"prompt": prompt, "verify": "python3 check.py", "tags": ["python", "refactor", "multi-file"]},
        fixture  = fixture,
        solution = solution,
        reading  = sorted(p for p in solution if p in fixture),
    )
