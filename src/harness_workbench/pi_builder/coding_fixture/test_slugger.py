import unittest

from slugger import slugify


class SlugifyTests(unittest.TestCase):
    def test_simple_words(self):
        self.assertEqual("harness-workbench", slugify("Harness Workbench"))

    def test_collapses_whitespace_and_punctuation(self):
        self.assertEqual("pi-adapter-ready", slugify("  Pi\tadapter... ready!  "))

    def test_empty_label(self):
        self.assertEqual("", slugify(" -- "))


if __name__ == "__main__":
    unittest.main()
