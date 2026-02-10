"""Unit tests for reddit activity helpers."""

import unittest

from tests._env import ensure_test_env

ensure_test_env()

from temporal.activities.reddit import normalize_username, should_process_redditor


class _LazyRedditor:
    def __init__(self, name: str | None):
        self.name = name
        self.unexpected_attr_lookups: list[str] = []

    def __getattr__(self, item):
        self.unexpected_attr_lookups.append(item)
        raise AssertionError(f"Unexpected lazy attribute lookup: {item}")


class RedditHelperTests(unittest.TestCase):
    def test_normalize_username_handles_prefix_case_and_whitespace(self):
        self.assertEqual(normalize_username("  /u/SomeUser  "), "someuser")
        self.assertEqual(normalize_username("u/AnotherUser"), "anotheruser")
        self.assertEqual(normalize_username("MiXeDcAsE"), "mixedcase")

    def test_should_process_redditor_filters_bot_username_case_insensitive(self):
        redditor = _LazyRedditor("/u/TradeBot")
        self.assertFalse(should_process_redditor(redditor, "tradebot"))
        self.assertEqual(redditor.unexpected_attr_lookups, [])

    def test_should_process_redditor_rejects_missing_author(self):
        self.assertFalse(should_process_redditor(None, "tradebot"))

    def test_should_process_redditor_accepts_real_user(self):
        redditor = _LazyRedditor("HelpfulTrader")
        self.assertTrue(should_process_redditor(redditor, "tradebot"))
        self.assertEqual(redditor.unexpected_attr_lookups, [])


if __name__ == "__main__":
    unittest.main()
