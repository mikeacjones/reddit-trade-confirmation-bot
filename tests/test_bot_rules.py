import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bot.rules import (  # noqa: E402
    build_confirmation_key,
    format_flair_from_template,
    is_confirming_trade,
    parse_trade_count,
)


class BotRulesTest(unittest.TestCase):
    def test_is_confirming_trade_is_case_insensitive(self):
        self.assertTrue(is_confirming_trade("Confirmed"))
        self.assertTrue(is_confirming_trade("trade CONFIRMED thanks"))
        self.assertFalse(is_confirming_trade("approval pending"))

    def test_build_confirmation_key_normalizes_case(self):
        self.assertEqual(
            build_confirmation_key("AbC123", "SomeUser"),
            "abc123:someuser",
        )

    def test_parse_trade_count_handles_empty_and_untracked_flair(self):
        self.assertEqual(parse_trade_count(None), 0)
        self.assertEqual(parse_trade_count(""), 0)
        self.assertEqual(parse_trade_count("Trades: 42"), 42)
        self.assertIsNone(parse_trade_count("Trusted Trader"))

    def test_format_flair_from_template_replaces_trade_range(self):
        self.assertEqual(
            format_flair_from_template("Collector | Trades: 11-50", 27),
            "Collector | Trades: 27",
        )
        self.assertEqual(
            format_flair_from_template("Custom Flair", 27),
            "Custom Flair",
        )


if __name__ == "__main__":
    unittest.main()
