import unittest

from intervals import merge_intervals


class MergeIntervalsTest(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(merge_intervals([]), [])

    def test_disjoint_stay_apart(self) -> None:
        self.assertEqual(merge_intervals([(1, 2), (4, 5)]), [(1, 2), (4, 5)])

    def test_overlapping_merge(self) -> None:
        self.assertEqual(merge_intervals([(1, 5), (3, 8)]), [(1, 8)])

    def test_touching_merge(self) -> None:
        self.assertEqual(merge_intervals([(1, 2), (2, 3)]), [(1, 3)])

    def test_contained_interval_keeps_outer_end(self) -> None:
        self.assertEqual(merge_intervals([(1, 10), (2, 3)]), [(1, 10)])
        self.assertEqual(merge_intervals([(1, 10), (2, 3), (4, 12)]), [(1, 12)])

    def test_unsorted_input(self) -> None:
        self.assertEqual(merge_intervals([(8, 9), (1, 3), (2, 4)]), [(1, 4), (8, 9)])

    def test_input_not_modified(self) -> None:
        data = [(5, 6), (1, 2)]
        merge_intervals(data)
        self.assertEqual(data, [(5, 6), (1, 2)])

    def test_single_point_interval(self) -> None:
        self.assertEqual(merge_intervals([(3, 3)]), [(3, 3)])

    def test_invalid_interval_raises(self) -> None:
        with self.assertRaises(ValueError):
            merge_intervals([(5, 1)])


if __name__ == "__main__":
    unittest.main()
