"""Helpers that are not about money."""

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
