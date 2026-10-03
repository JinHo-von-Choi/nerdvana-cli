"""Task long-bughunt-ledger: two defects four modules deep behind failing statement tests, among look-alike modules.

Author: 최진호
Date:   2026-10-03

The statement tests fail; the causes live in ledgerlib/engine/ratetable.py (a rate takes effect on
its date, inclusive) and ledgerlib/engine/conversion.py (amounts are scaled by the currency's own
number of minor-unit digits). Thirty look-alike modules use the same idioms correctly.
"""

from __future__ import annotations

import random

from .common import TaskBuild, prose, rng_for

TASK_ID = "long-bughunt-ledger"

INIT = '"""ledgerlib: a small multi-currency ledger."""\n'

MODEL = '''"""Plain records of the ledger."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Transaction:
    """One posting in the currency's minor units (cents, or whole yen for JPY)."""

    day:          str
    currency:     str
    amount_minor: int
    memo:         str = ""


@dataclass
class Account:
    """An account whose statements are kept in USD."""

    account_id:   str
    owner:        str
    transactions: list[Transaction] = field(default_factory=list)
'''

MONEY = '''"""Currency digits and plain text amounts."""

from __future__ import annotations

EXPONENTS = {"USD": 2, "EUR": 2, "GBP": 2, "JPY": 0, "KWD": 3}


def exponent(currency: str) -> int:
    """Number of minor-unit digits of a currency."""
    return EXPONENTS[currency]


def format_minor(amount_minor: int, currency: str) -> str:
    """Text of an amount given in minor units, with the currency's own number of decimals."""
    digits = exponent(currency)
    sign   = "-" if amount_minor < 0 else ""
    whole, fraction = divmod(abs(amount_minor), 10 ** digits)
    return f"{sign}{whole:,}" + (f".{fraction:0{digits}d}" if digits else "")
'''

RATES = '''"""Exchange rates: USD per one major unit, each valid from its effective date (inclusive) until the next entry."""

BASE = "USD"

RATES = {
    "EUR": [("2026-01-01", "1.10"), ("2026-02-01", "1.20"), ("2026-03-01", "1.15")],
    "GBP": [("2026-01-01", "1.30"), ("2026-02-15", "1.35")],
    "JPY": [("2026-01-01", "0.0070"), ("2026-03-10", "0.0065")],
    "KWD": [("2026-01-01", "3.25")],
}
'''

RATETABLE = '''"""Rate lookup by day."""

from __future__ import annotations

from bisect import bisect_left
from decimal import Decimal

from ledgerlib.data.rates import BASE, RATES


def rate_on(currency: str, day: str) -> Decimal:
    """USD per one major unit of the currency on an ISO day.

    A rate is in force from its effective date, inclusive, until the next effective date.
    """
    if currency == BASE:
        return Decimal(1)
    entries = RATES[currency]
    dates   = [effective for effective, _ in entries]
    index   = bisect_left(dates, day) - 1
    if index < 0:
        raise LookupError(f"no rate for {currency} on {day}")
    return Decimal(entries[index][1])
'''

RATETABLE_FIXED = RATETABLE.replace("from bisect import bisect_left", "from bisect import bisect_right").replace("bisect_left(dates, day) - 1", "bisect_right(dates, day) - 1")

CONVERSION = '''"""Conversion of postings into USD cents."""

from __future__ import annotations

from decimal import Decimal

from ledgerlib.data.rates import BASE
from ledgerlib.engine.ratetable import rate_on
from ledgerlib.engine.rounding import round_half_up


def convert_minor(amount_minor: int, currency: str, day: str) -> int:
    """USD cents worth of an amount given in the minor units of a currency on a day.

    The amount is first turned into major units using the currency's own number of digits.
    """
    if currency == BASE:
        return amount_minor
    major = Decimal(amount_minor) / (10 ** 2)
    return round_half_up(major * rate_on(currency, day) * 100)
'''

CONVERSION_FIXED = CONVERSION.replace("from ledgerlib.data.rates import BASE\n", "from ledgerlib.data.rates import BASE\n").replace(
    "from ledgerlib.engine.rounding import round_half_up\n", "from ledgerlib.engine.rounding import round_half_up\nfrom ledgerlib.money import exponent\n").replace(
    "(10 ** 2)", "(10 ** exponent(currency))")

ROUNDING = '''"""Rounding of decimal amounts."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def round_half_up(value: Decimal) -> int:
    """Nearest whole number, halves away from zero."""
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
'''

FEES = '''"""Fees on large postings."""

from __future__ import annotations

from decimal import Decimal

FEE_FREE_BELOW = 100_000
FEE_RATE       = Decimal("0.0025")


def fee_for(usd_cents: int) -> int:
    """Fee in USD cents. Postings below 1000 USD carry none; above, a quarter of a percent, truncated by design."""
    if abs(usd_cents) < FEE_FREE_BELOW:
        return 0
    return int(abs(usd_cents) * FEE_RATE)
'''

INTEREST = '''"""Interest tiers."""

from __future__ import annotations

from bisect import bisect_left

THRESHOLDS = [100_000, 1_000_000, 10_000_000]
RATES_BP   = [10, 25, 40, 55]


def tier_rate_bp(balance_cents: int) -> int:
    """Basis points earned at a balance. A balance equal to a threshold stays in the lower tier, by design."""
    return RATES_BP[bisect_left(THRESHOLDS, balance_cents)]
'''

LEGACY_RATETABLE = '''"""Earlier rate lookup, kept for the migration scripts."""

from __future__ import annotations

from bisect import bisect_left
from decimal import Decimal

from ledgerlib.data.rates import RATES


def rate_on_legacy(currency: str, day: str) -> Decimal:
    """Rate in force before the day (exclusive). Only the migration scripts use this."""
    # FIXME: boundary handling differs from ratetable.rate_on on purpose, see the migration notes.
    entries = RATES[currency]
    index   = bisect_left([d for d, _ in entries], day) - 1
    return Decimal(entries[max(index, 0)][1])
'''

BALANCE = '''"""Running and closing balances in USD cents."""

from __future__ import annotations

from ledgerlib.engine.conversion import convert_minor
from ledgerlib.engine.fees import fee_for
from ledgerlib.model import Account, Transaction


def running_balance(account: Account) -> list[tuple[Transaction, int, int]]:
    """Each transaction in day order with its USD cents and the balance after it."""
    rows, total = [], 0
    for tx in sorted(account.transactions, key=lambda t: t.day):
        usd = convert_minor(tx.amount_minor, tx.currency, tx.day)
        total += usd - fee_for(usd)
        rows.append((tx, usd, total))
    return rows


def closing_balance(account: Account) -> int:
    """Balance after the last transaction, zero for an account without any."""
    rows = running_balance(account)
    return rows[-1][2] if rows else 0
'''

STATEMENT = '''"""Plain text account statements."""

from __future__ import annotations

from ledgerlib.engine.balance import closing_balance, running_balance
from ledgerlib.model import Account
from ledgerlib.money import format_minor


def render_statement(account: Account) -> str:
    """Statement text with one line per transaction and the closing balance in USD."""
    lines = [f"Statement for {account.owner} ({account.account_id})"]
    for tx, usd, _ in running_balance(account):
        lines.append(f"{tx.day}  {tx.currency}  {format_minor(tx.amount_minor, tx.currency):>12}  USD {format_minor(usd, 'USD'):>10}")
    lines.append(f"Closing balance: USD {format_minor(closing_balance(account), 'USD')}")
    return "\\n".join(lines) + "\\n"
'''

TEST_STATEMENT = '''"""Statements and balances."""

import unittest

from ledgerlib.engine.balance import closing_balance
from ledgerlib.model import Account, Transaction
from ledgerlib.reports.statement import render_statement


def account(*postings):
    return Account("A-1", "Ada", [Transaction(day, currency, amount) for day, currency, amount in postings])


class StatementTests(unittest.TestCase):
    def test_usd_only(self):
        acct = account(("2026-01-05", "USD", 2500), ("2026-01-09", "USD", -400))
        self.assertEqual(closing_balance(acct), 2100)
        self.assertIn("Closing balance: USD 21.00", render_statement(acct))

    def test_euro_postings_use_the_rate_of_their_day(self):
        acct = account(("2026-01-15", "EUR", 5000), ("2026-02-01", "EUR", 10000))
        self.assertEqual(closing_balance(acct), 5500 + 12000)
        self.assertIn("Closing balance: USD 175.00", render_statement(acct))

    def test_yen_has_no_minor_digits(self):
        acct = account(("2026-01-10", "JPY", 10000), ("2026-01-20", "USD", 2500))
        self.assertEqual(closing_balance(acct), 7000 + 2500)
        self.assertIn("Closing balance: USD 95.00", render_statement(acct))


if __name__ == "__main__":
    unittest.main()
'''

TEST_ENGINE = '''"""Engine pieces that are not about rates."""

import unittest
from decimal import Decimal

from ledgerlib.engine.fees import fee_for
from ledgerlib.engine.interest import tier_rate_bp
from ledgerlib.engine.ratetable import rate_on
from ledgerlib.engine.rounding import round_half_up
from ledgerlib.money import format_minor


class EngineTests(unittest.TestCase):
    def test_fee_free_below_the_limit(self):
        self.assertEqual(fee_for(99_999), 0)
        self.assertEqual(fee_for(200_000), 500)

    def test_interest_tiers(self):
        self.assertEqual(tier_rate_bp(100_000), 10)
        self.assertEqual(tier_rate_bp(100_001), 25)

    def test_rate_between_two_dates(self):
        self.assertEqual(rate_on("EUR", "2026-01-20"), Decimal("1.10"))
        self.assertEqual(rate_on("EUR", "2026-02-20"), Decimal("1.20"))
        self.assertEqual(rate_on("USD", "2026-02-20"), Decimal(1))

    def test_rounding(self):
        self.assertEqual(round_half_up(Decimal("2.5")), 3)
        self.assertEqual(round_half_up(Decimal("-2.5")), -3)

    def test_format(self):
        self.assertEqual(format_minor(123456, "USD"), "1,234.56")
        self.assertEqual(format_minor(5000, "JPY"), "5,000")
        self.assertEqual(format_minor(-1250, "KWD"), "-1.250")


if __name__ == "__main__":
    unittest.main()
'''

TEST_IMPORTS = '''"""Every module of the package imports."""

import importlib
import pkgutil
import unittest

import ledgerlib


class ImportTests(unittest.TestCase):
    def test_every_module_imports(self):
        names = [m.name for m in pkgutil.walk_packages(ledgerlib.__path__, "ledgerlib.")]
        self.assertGreater(len(names), 30)
        for name in names:
            importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
'''

WORDS  = ["settle", "match", "batch", "ledger", "window", "tier", "quota", "digest", "cycle", "margin", "slot", "hold"]
VERBS  = ["clamp", "bucket", "shift", "scale", "tally", "limit", "split", "merge"]
PACKAGES = {
    "adapters": ["bank_a", "bank_b", "bank_c", "csv_in", "json_in", "swift_in", "card_feed", "payroll_feed"],
    "util":     ["strings", "dates", "validation", "numbers", "lists", "tables", "windows", "retry"],
    "reports":  ["summary", "aging", "export", "daily", "monthly", "audit"],
    "engine":   ["limits", "schedule", "holds", "batching", "netting", "reserve"],
}


def _function(rng: random.Random, module: str, k: int) -> tuple[str, str]:
    """A small correct function, and the module level constant it needs (possibly empty)."""
    name = f"{rng.choice(VERBS)}_{rng.choice(WORDS)}_{k}"
    doc  = prose(rng, 2)
    kind = rng.randrange(5)
    if kind == 0:
        return f'def {name}(values: list[int], limit: int = {rng.randrange(50, 900)}) -> list[int]:\n    """{doc}"""\n    return [min(v, limit) for v in values]\n', ""
    if kind == 1:
        const = f"{name.upper()}_CUTS = [{', '.join(str(n) for n in sorted(rng.sample(range(100, 90000), 3)))}]\n"
        return (f'def {name}(amount: int) -> int:\n    """{doc} A value equal to a cut belongs to the lower bucket."""\n'
                f"    return bisect_left({name.upper()}_CUTS, amount)\n"), const
    if kind == 2:
        return f'def {name}(cents: int) -> str:\n    """{doc}"""\n    whole, fraction = divmod(abs(cents), 100)\n    return ("-" if cents < 0 else "") + f"{{whole}}.{{fraction:02d}}"\n', ""
    if kind == 3:
        return f'def {name}(day: str) -> str:\n    """{doc}"""\n    year, month, _ = day.split("-")\n    return f"{{year}}-{{month}}"\n', ""
    bp = rng.randrange(5, 90)
    return f'def {name}(amount: int, rate_bp: int = {bp}) -> int:\n    """{doc}"""\n    return (amount * rate_bp + 5000) // 10000\n', ""


def filler_module(rng: random.Random, package: str, name: str) -> str:
    """A look-alike module that uses the same idioms correctly."""
    funcs, consts = zip(*[_function(rng, name, k) for k in range(10)], strict=True)
    notes = "".join(f'NOTE_{i + 1} = (\n    "{prose(rng, 3)}"\n)\n' for i in range(14))
    head  = f'"""{package} {name}.\n\n{prose(rng, 5)}\n"""\n\nfrom __future__ import annotations\n\nfrom bisect import bisect_left\n\n'
    return head + notes + "\n" + "".join(c for c in consts if c) + "\n\n" + "\n\n".join(funcs)


def build() -> TaskBuild:
    rng     = rng_for(TASK_ID)
    fixture = {
        "ledgerlib/__init__.py": INIT, "ledgerlib/model.py": MODEL, "ledgerlib/money.py": MONEY,
        "ledgerlib/data/__init__.py": "", "ledgerlib/data/rates.py": RATES,
        "ledgerlib/engine/__init__.py": "", "ledgerlib/engine/ratetable.py": RATETABLE, "ledgerlib/engine/conversion.py": CONVERSION,
        "ledgerlib/engine/rounding.py": ROUNDING, "ledgerlib/engine/fees.py": FEES, "ledgerlib/engine/interest.py": INTEREST,
        "ledgerlib/engine/ratetable_legacy.py": LEGACY_RATETABLE, "ledgerlib/engine/balance.py": BALANCE,
        "ledgerlib/reports/__init__.py": "", "ledgerlib/reports/statement.py": STATEMENT,
        "ledgerlib/adapters/__init__.py": "", "ledgerlib/util/__init__.py": "",
        "tests/__init__.py": "", "tests/test_statement.py": TEST_STATEMENT, "tests/test_engine.py": TEST_ENGINE, "tests/test_imports.py": TEST_IMPORTS,
    }
    for package, names in PACKAGES.items():
        for name in names:
            fixture[f"ledgerlib/{package}/{name}.py"] = filler_module(rng, package, name)
    solution = {"ledgerlib/engine/ratetable.py": RATETABLE_FIXED, "ledgerlib/engine/conversion.py": CONVERSION_FIXED}
    prompt = ("`python3 -m unittest discover -s tests -t .` fails in tests/test_statement.py: statement balances are wrong for some postings "
              "(the USD-only test passes). Find the root causes inside the package and fix them in the code, not in the tests, "
              "so that the whole suite passes. The package has many look-alike modules; change only what is actually wrong.")
    return TaskBuild(
        task_id  = TASK_ID,
        task     = {"prompt": prompt, "verify": "python3 -m unittest discover -s tests -t .", "tags": ["python", "bugfix", "multi-file"]},
        fixture  = fixture,
        solution = solution,
        reading  = sorted(path for path in fixture if path.startswith("ledgerlib/") and path.endswith(".py")),
    )
