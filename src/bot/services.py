"""Application services for the trade confirmation bot."""

from dataclasses import asdict

from .models import (
    CommentData,
    FlairIncrementRequest,
    FlairIncrementResult,
    ReplyToCommentInput,
    ValidationResult,
)
from .rules import build_confirmation_key


class ConfirmationService:
    """Prepare confirmation-related replies, requests, and workflow results."""

    @staticmethod
    def build_invalid_reply(
        comment_data: CommentData,
        validation: ValidationResult,
    ) -> ReplyToCommentInput | None:
        """Build the reply payload for a rejected confirmation."""
        if not validation.reason:
            return None

        return ReplyToCommentInput(
            comment_id=comment_data.id,
            template_name=validation.reason,
            format_args={
                **asdict(comment_data),
                "parent_author": validation.parent_author,
                "parent_comment_id": validation.parent_comment_id,
            },
        )

    @staticmethod
    def build_flair_increment_requests(
        validation: ValidationResult,
    ) -> tuple[FlairIncrementRequest, FlairIncrementRequest]:
        """Build paired flair increment requests for a confirmed trade."""
        parent_author = validation.parent_author
        confirmer = validation.confirmer
        if parent_author is None or confirmer is None:
            raise ValueError("Confirmed validation must include parent_author and confirmer")

        confirmation_key = build_confirmation_key(validation.parent_comment_id, confirmer)
        return (
            FlairIncrementRequest(
                username=parent_author,
                request_id=f"{confirmation_key}:parent",
            ),
            FlairIncrementRequest(
                username=confirmer,
                request_id=f"{confirmation_key}:confirmer",
            ),
        )

    @staticmethod
    def build_confirmation_reply(
        fallback_comment_id: str,
        validation: ValidationResult,
        parent_result: FlairIncrementResult,
        confirmer_result: FlairIncrementResult,
    ) -> ReplyToCommentInput:
        """Build the reply payload for a successful confirmation."""
        reply_comment_id = validation.reply_to_comment_id or fallback_comment_id
        return ReplyToCommentInput(
            comment_id=reply_comment_id,
            template_name="trade_confirmation",
            format_args={
                "comment_id": reply_comment_id,
                "confirmer": validation.confirmer,
                "parent_author": validation.parent_author,
                "old_comment_flair": confirmer_result.old_flair or "unknown",
                "new_comment_flair": confirmer_result.new_flair or "unknown",
                "old_parent_flair": parent_result.old_flair or "unknown",
                "new_parent_flair": parent_result.new_flair or "unknown",
            },
        )

    @staticmethod
    def build_confirmed_result(
        comment_id: str,
        validation: ValidationResult,
        parent_result: FlairIncrementResult,
        confirmer_result: FlairIncrementResult,
    ) -> dict:
        """Build the workflow result for a successful confirmation."""
        return {
            "status": "confirmed",
            "comment_id": comment_id,
            "parent_author": validation.parent_author,
            "confirmer": validation.confirmer,
            "parent_new_flair": parent_result.new_flair,
            "confirmer_new_flair": confirmer_result.new_flair,
        }
