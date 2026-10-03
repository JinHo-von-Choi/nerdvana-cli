import unittest

import customers


class CustomersTest(unittest.TestCase):
    def test_register_normalizes_phone(self) -> None:
        self.assertEqual(customers.register(" Ann ", "(555) 123-4567"), {"kind": "customer", "name": "Ann", "phone": "+15551234567"})

    def test_eleven_digits(self) -> None:
        self.assertEqual(customers.register("Bo", "1-555-123-4567")["phone"], "+15551234567")

    def test_bad_phone(self) -> None:
        with self.assertRaises(ValueError):
            customers.register("Cy", "12345")
