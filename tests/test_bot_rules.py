import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bot.models import CommentData, ConfirmationContext  # noqa: E402
from bot.rules import (  # noqa: E402
    build_confirmation_key,
    evaluate_confirmation,
    find_flair_template,
    format_flair_from_template,
    is_confirming_trade,
    is_possible_watermark_gap,
    parse_trade_count,
    should_include_comment,
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


    def test_should_include_root_on_previous_submission(self):
        self.assertTrue(
            should_include_comment(
                submission_id="prev1",
                current_submission_id="cur1",
                is_root=True,
                body_lower="looking to trade",
            )
        )

    def test_should_exclude_root_on_current_submission(self):
        self.assertFalse(
            should_include_comment(
                submission_id="cur1",
                current_submission_id="cur1",
                is_root=True,
                body_lower="looking to trade",
            )
        )

    def test_should_include_non_root_confirmed(self):
        self.assertTrue(
            should_include_comment(
                submission_id="cur1",
                current_submission_id="cur1",
                is_root=False,
                body_lower="confirmed",
            )
        )

    def test_should_include_non_root_approved(self):
        self.assertTrue(
            should_include_comment(
                submission_id="cur1",
                current_submission_id="cur1",
                is_root=False,
                body_lower="mod approved this trade",
            )
        )

    def test_should_exclude_non_root_irrelevant(self):
        self.assertFalse(
            should_include_comment(
                submission_id="cur1",
                current_submission_id="cur1",
                is_root=False,
                body_lower="thanks for the trade",
            )
        )

    def test_is_possible_watermark_gap_all_conditions_met(self):
        self.assertTrue(
            is_possible_watermark_gap(
                had_initial_watermark=True,
                found_seen=False,
                listing_exhausted=True,
                scanned_count=1000,
                gap_threshold=900,
            )
        )

    def test_is_possible_watermark_gap_found_seen(self):
        self.assertFalse(
            is_possible_watermark_gap(
                had_initial_watermark=True,
                found_seen=True,
                listing_exhausted=True,
                scanned_count=1000,
                gap_threshold=900,
            )
        )

    def test_is_possible_watermark_gap_below_threshold(self):
        self.assertFalse(
            is_possible_watermark_gap(
                had_initial_watermark=True,
                found_seen=False,
                listing_exhausted=True,
                scanned_count=100,
                gap_threshold=900,
            )
        )



_DEFAULT_COMMENT = CommentData(
    id="c1",
    body="confirmed",
    body_html="<p>confirmed</p>",
    author_name="Buyer",
    author_flair_text="Trades: 4",
    permalink="/r/test/comments/c1",
    created_utc=1.0,
    is_root=False,
    parent_id="t1_parent",
    submission_id="s1",
    saved=False,
)

_DEFAULT_CONTEXT = ConfirmationContext(
    parent_exists=True,
    parent_is_banned=False,
    parent_is_processable=True,
    parent_author_name="Seller",
    parent_id="p1",
    parent_is_root=True,
    parent_is_saved=False,
    parent_body_lower="trading with u/buyer",
    parent_body_html_lower='<p>trading with <a href="/u/buyer">u/buyer</a></p>',
    is_moderator=False,
)


def _make_comment(**overrides: object) -> CommentData:
    """Helper to build a CommentData with sensible defaults."""
    return replace(_DEFAULT_COMMENT, **overrides)


def _make_context(**overrides: object) -> ConfirmationContext:
    """Helper to build a valid parent context with sensible defaults."""
    return replace(_DEFAULT_CONTEXT, **overrides)


class EvaluateConfirmationTest(unittest.TestCase):
    def test_root_comment_is_skipped(self):
        result = evaluate_confirmation(
            _make_comment(is_root=True),
            _make_context(),
        )
        self.assertFalse(result.valid)

    def test_no_parent_is_skipped(self):
        result = evaluate_confirmation(
            _make_comment(),
            ConfirmationContext(parent_exists=False),
        )
        self.assertFalse(result.valid)

    def test_banned_parent_is_skipped(self):
        result = evaluate_confirmation(
            _make_comment(),
            _make_context(parent_is_banned=True),
        )
        self.assertFalse(result.valid)

    def test_unprocessable_parent_is_skipped(self):
        result = evaluate_confirmation(
            _make_comment(),
            _make_context(parent_is_processable=False),
        )
        self.assertFalse(result.valid)

    def test_self_trade_is_skipped(self):
        result = evaluate_confirmation(
            _make_comment(author_name="Seller"),
            _make_context(parent_author_name="Seller"),
        )
        self.assertFalse(result.valid)

    def test_not_confirming_trade_is_skipped(self):
        result = evaluate_confirmation(
            _make_comment(body="thanks for the trade"),
            _make_context(),
        )
        self.assertFalse(result.valid)

    def test_already_confirmed_is_rejected(self):
        result = evaluate_confirmation(
            _make_comment(),
            _make_context(parent_is_saved=True),
        )
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "already_confirmed")
        self.assertEqual(result.parent_author, "Seller")

    def test_username_not_in_parent_is_rejected(self):
        result = evaluate_confirmation(
            _make_comment(author_name="Buyer"),
            _make_context(
                parent_body_lower="trading with someone",
                parent_body_html_lower="<p>trading with someone</p>",
            ),
        )
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "cant_confirm_username")

    def test_valid_confirmation(self):
        result = evaluate_confirmation(
            _make_comment(author_name="Buyer"),
            _make_context(),
        )
        self.assertTrue(result.valid)
        self.assertEqual(result.parent_author, "Seller")
        self.assertEqual(result.confirmer, "Buyer")
        self.assertEqual(result.parent_comment_id, "p1")

    def test_mod_approval_valid(self):
        result = evaluate_confirmation(
            _make_comment(body="approved"),
            _make_context(
                parent_is_root=False,
                parent_author_name="Confirmer",
                parent_id="conf1",
                is_moderator=True,
                grandparent_exists=True,
                grandparent_is_root=True,
                grandparent_author_name="OriginalPoster",
                grandparent_id="gp1",
            ),
        )
        self.assertTrue(result.valid)
        self.assertTrue(result.is_mod_approval)
        self.assertEqual(result.parent_author, "OriginalPoster")
        self.assertEqual(result.confirmer, "Confirmer")
        self.assertEqual(result.parent_comment_id, "gp1")
        self.assertEqual(result.reply_to_comment_id, "conf1")

    def test_mod_approval_non_mod_is_skipped(self):
        result = evaluate_confirmation(
            _make_comment(body="approved"),
            _make_context(
                parent_is_root=False,
                is_moderator=False,
                grandparent_exists=True,
                grandparent_is_root=True,
            ),
        )
        self.assertFalse(result.valid)

    def test_mod_approval_grandparent_not_root_is_skipped(self):
        result = evaluate_confirmation(
            _make_comment(body="approved"),
            _make_context(
                parent_is_root=False,
                is_moderator=True,
                grandparent_exists=True,
                grandparent_is_root=False,
            ),
        )
        self.assertFalse(result.valid)


class FindFlairTemplateTest(unittest.TestCase):
    def test_finds_matching_template(self):
        templates = {
            (0, 10): {"id": "t1", "template": "Trades: 0-10", "mod_only": False},
            (11, 50): {"id": "t2", "template": "Trades: 11-50", "mod_only": False},
        }
        result = find_flair_template(templates, 5, is_moderator=False)
        assert result is not None
        self.assertEqual(result["id"], "t1")

    def test_finds_higher_range(self):
        templates = {
            (0, 10): {"id": "t1", "template": "Trades: 0-10", "mod_only": False},
            (11, 50): {"id": "t2", "template": "Trades: 11-50", "mod_only": False},
        }
        result = find_flair_template(templates, 25, is_moderator=False)
        assert result is not None
        self.assertEqual(result["id"], "t2")

    def test_no_match_returns_none(self):
        templates = {
            (0, 10): {"id": "t1", "template": "Trades: 0-10", "mod_only": False},
        }
        result = find_flair_template(templates, 99, is_moderator=False)
        self.assertIsNone(result)

    def test_mod_only_template_skipped_for_non_mod(self):
        templates = {
            (0, 10): {"id": "t1", "template": "Trades: 0-10", "mod_only": True},
        }
        result = find_flair_template(templates, 5, is_moderator=False)
        self.assertIsNone(result)

    def test_mod_only_template_matched_for_mod(self):
        templates = {
            (0, 10): {"id": "t1", "template": "Mod Trades: 0-10", "mod_only": True},
        }
        result = find_flair_template(templates, 5, is_moderator=True)
        assert result is not None
        self.assertEqual(result["id"], "t1")


if __name__ == "__main__":
    unittest.main()
