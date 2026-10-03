import unittest

from inventory import Inventory


class InventoryTest(unittest.TestCase):
    def test_add_accumulates(self) -> None:
        inv = Inventory()
        self.assertEqual(inv.add("bolt", 3), 3)
        self.assertEqual(inv.add("bolt", 2), 5)
        self.assertEqual(inv.count("bolt"), 5)

    def test_unknown_item_counts_zero(self) -> None:
        self.assertEqual(Inventory().count("nut"), 0)

    def test_add_rejects_non_positive(self) -> None:
        with self.assertRaises(ValueError):
            Inventory().add("bolt", 0)


if __name__ == "__main__":
    unittest.main()
