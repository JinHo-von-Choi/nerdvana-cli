import unittest

import suppliers


class SuppliersTest(unittest.TestCase):
    def test_register_defaults(self) -> None:
        self.assertEqual(
            suppliers.register("Acme", "555.123.4567"),
            {"kind": "supplier", "name": "Acme", "phone": "+15551234567", "terms_days": 30},
        )

    def test_terms(self) -> None:
        self.assertEqual(suppliers.register("Acme", "5551234567", 60)["terms_days"], 60)

    def test_bad_phone(self) -> None:
        with self.assertRaises(ValueError):
            suppliers.register("Acme", "abc")
