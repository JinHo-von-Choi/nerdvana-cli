"""Engine pieces that are not about rates."""

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
