import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bot.models import (  # noqa: E402
    CommentData,
    FlairIncrementResult,
    ValidationResult,
)
from bot.services import ConfirmationService  # noqa: E402


class ConfirmationServiceTest(unittest.TestCase):
    def test_build_invalid_reply_uses_comment_and_validation_context(self):
        comment = CommentData(
            id="c1",
            body="confirmed",
            author_name="Confirmer",
            created_utc=1.0,
            is_root=False,
            submission_id="s1",
        )
        validation = ValidationResult(
            valid=False,
            reason="cant_confirm_username",
            parent_author="Seller",
            parent_comment_id="parent1",
        )

        reply = ConfirmationService.build_invalid_reply(comment, validation)

        self.assertIsNotNone(reply)
        assert reply is not None
        assert reply.format_args is not None
        self.assertEqual(reply.comment_id, "c1")
        self.assertEqual(reply.template_name, "cant_confirm_username")
        self.assertEqual(reply.format_args["parent_author"], "Seller")
        self.assertEqual(reply.format_args["parent_comment_id"], "parent1")
        self.assertEqual(reply.format_args["author_name"], "Confirmer")

    def test_build_invalid_reply_returns_none_for_skip(self):
        comment = CommentData(
            id="c1",
            body="noop",
            author_name="User",
            created_utc=1.0,
            is_root=False,
            submission_id="s1",
        )
        validation = ValidationResult(valid=False)

        self.assertIsNone(ConfirmationService.build_invalid_reply(comment, validation))

    def test_build_flair_increment_requests_uses_confirmation_key_suffixes(self):
        validation = ValidationResult(
            valid=True,
            parent_author="Seller",
            confirmer="Buyer",
            parent_comment_id="AbC123",
        )

        parent_req, confirmer_req = ConfirmationService.build_flair_increment_requests(
            validation
        )

        self.assertEqual(parent_req.username, "Seller")
        self.assertEqual(parent_req.request_id, "abc123:buyer:parent")
        self.assertEqual(confirmer_req.username, "Buyer")
        self.assertEqual(confirmer_req.request_id, "abc123:buyer:confirmer")

    def test_build_confirmation_reply_prefers_reply_to_comment_id(self):
        validation = ValidationResult(
            valid=True,
            parent_author="Seller",
            confirmer="Buyer",
            reply_to_comment_id="reply123",
        )
        parent_result = FlairIncrementResult(
            old_flair="Trades: 2",
            new_flair="Trades: 3",
        )
        confirmer_result = FlairIncrementResult(
            old_flair="Trades: 4",
            new_flair="Trades: 5",
        )

        reply = ConfirmationService.build_confirmation_reply(
            "fallback",
            validation,
            parent_result,
            confirmer_result,
        )

        self.assertEqual(reply.comment_id, "reply123")
        self.assertEqual(reply.template_name, "trade_confirmation")
        assert reply.format_args is not None
        self.assertEqual(reply.format_args["parent_author"], "Seller")
        self.assertEqual(reply.format_args["confirmer"], "Buyer")
        self.assertEqual(reply.format_args["new_parent_flair"], "Trades: 3")
        self.assertEqual(reply.format_args["new_comment_flair"], "Trades: 5")

    def test_build_confirmed_result_returns_expected_shape(self):
        validation = ValidationResult(
            valid=True,
            parent_author="Seller",
            confirmer="Buyer",
        )
        parent_result = FlairIncrementResult(
            new_flair="Trades: 3",
        )
        confirmer_result = FlairIncrementResult(
            new_flair="Trades: 5",
        )

        result = ConfirmationService.build_confirmed_result(
            "comment1",
            validation,
            parent_result,
            confirmer_result,
        )

        self.assertEqual(
            result,
            {
                "status": "confirmed",
                "comment_id": "comment1",
                "parent_author": "Seller",
                "confirmer": "Buyer",
                "parent_new_flair": "Trades: 3",
                "confirmer_new_flair": "Trades: 5",
            },
        )


if __name__ == "__main__":
    unittest.main()
