import unittest

from slug import slugify


class SlugTest(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertEqual(slugify("Hello, World!"), "hello-world")

    def test_collapses_runs_and_trims(self) -> None:
        self.assertEqual(slugify("  --A  b--  "), "a-b")

    def test_empty(self) -> None:
        self.assertEqual(slugify("!!!"), "")


if __name__ == "__main__":
    unittest.main()
