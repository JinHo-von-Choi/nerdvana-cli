import unittest

from textutil import word_count


class WordCountTest(unittest.TestCase):
    def test_words(self) -> None:
        self.assertEqual(word_count("one  two\tthree\n"), 3)

    def test_empty(self) -> None:
        self.assertEqual(word_count(""), 0)
