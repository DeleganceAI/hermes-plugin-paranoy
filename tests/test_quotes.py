import unittest

from tests.support import load_plugin

plugin = load_plugin()
quote_in_evidence = plugin.quotes.quote_in_evidence


class QuoteTests(unittest.TestCase):
    def test_normalization_allows_the_documented_differences(self):
        evidence = 'The “pump” used 18 Wh — a soft\u00adhyphen and a ﬁle.'
        self.assertTrue(quote_in_evidence('the "pump" used 18 wh - a softhyphen and a file.', evidence))
        self.assertTrue(quote_in_evidence("THE   “PUMP”\nUSED 18 WH", evidence))

    def test_paraphrase_and_omitted_words_do_not_match(self):
        evidence = "During the two-hour test, the new pump used 18 Wh and the old pump used 24 Wh."
        self.assertFalse(quote_in_evidence("the new pump used less electricity", evidence))
        self.assertFalse(quote_in_evidence("the new pump used 18 Wh and 24 Wh", evidence))
        self.assertTrue(
            quote_in_evidence("the new pump used 18 wh and the old pump used 24 wh", evidence)
        )
        self.assertFalse(quote_in_evidence("", evidence))
        self.assertFalse(quote_in_evidence("   ", evidence))


if __name__ == "__main__":
    unittest.main()
