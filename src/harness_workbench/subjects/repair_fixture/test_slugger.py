import unittest

from slugger import slugify


class SlugifyTests(unittest.TestCase):
    def test_words(self) -> None:
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_punctuation(self) -> None:
        self.assertEqual(slugify("Hello, World!"), "hello-world")

    def test_runs_of_separators(self) -> None:
        self.assertEqual(slugify("  Already---spaced  "), "already-spaced")

    def test_symbol_separator(self) -> None:
        self.assertEqual(slugify("Rock & Roll"), "rock-roll")


if __name__ == "__main__":
    unittest.main()
