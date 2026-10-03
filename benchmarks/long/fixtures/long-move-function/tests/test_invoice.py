"""Invoice labels."""

import unittest
from unittest import mock

from acme.billing import invoice


class InvoiceTests(unittest.TestCase):
    def test_label_uses_the_money_formatter(self):
        with mock.patch("acme.legacy.helpers.format_money", return_value="FMT"):
            self.assertEqual(invoice.amount_label(5), "billing.invoice: FMT")

    def test_label_text(self):
        self.assertEqual(invoice.amount_label(5), "billing.invoice: $0.05")


if __name__ == "__main__":
    unittest.main()
