"""Statements and balances."""

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
