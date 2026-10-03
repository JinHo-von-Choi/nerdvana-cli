"""Money formatting and parsing."""

import unittest

from acme.money.formatting import CURRENCY_SYMBOLS, format_money, parse_money


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
