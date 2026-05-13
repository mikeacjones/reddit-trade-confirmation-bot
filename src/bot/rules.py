"""Pure business rules for the trade confirmation bot."""

import re

from .models import CommentData, ConfirmationContext, ValidationResult

FLAIR_PATTERN = re.compile(r"Trades: (\d+)")
FLAIR_TEMPLATE_PATTERN = re.compile(r"Trades: ((\d+)-(\d+))")


def is_confirming_trade(comment_body: str) -> bool:
    """Check if comment is confirming a trade."""
    return "confirmed" in comment_body.lower()


def build_confirmation_key(parent_comment_id: str | None, confirmer: str) -> str:
    """Build the idempotency key used for paired flair increments."""
    return f"{parent_comment_id}:{confirmer}".lower()


def parse_trade_count(flair_text: str | None) -> int | None:
    """Extract tracked trade count from flair text."""
    if not flair_text:
        return 0

    match = FLAIR_PATTERN.search(flair_text)
    return int(match.group(1)) if match else None


def format_flair_from_template(flair_template: str, count: int) -> str:
    """Replace the tracked trade range in a flair template with the exact count."""
    match = FLAIR_TEMPLATE_PATTERN.search(flair_template)
    if not match:
        return flair_template

    start, end = match.span(1)
    return flair_template[:start] + str(count) + flair_template[end:]


def should_include_comment(
    *,
    submission_id: str,
    current_submission_id: str,
    is_root: bool,
    body_lower: str,
) -> bool:
    """Decide whether a polled comment should be included for processing.

    Only called for comments that are on an active submission, not banned,
    and from a processable redditor.
    """
    if is_root:
        return submission_id != current_submission_id

    return "confirmed" in body_lower or "approved" in body_lower


def is_possible_watermark_gap(
    *,
    had_initial_watermark: bool,
    found_seen: bool,
    listing_exhausted: bool,
    scanned_count: int,
    gap_threshold: int,
) -> bool:
    """Detect when scanning reached the listing limit without finding known IDs."""
    return (
        had_initial_watermark
        and not found_seen
        and listing_exhausted
        and scanned_count >= gap_threshold
    )


def evaluate_confirmation(
    comment: CommentData,
    context: ConfirmationContext,
) -> ValidationResult:
    """Pure validation of a confirmation comment given pre-fetched context."""
    if comment.is_root:
        return ValidationResult(valid=False)

    if not context.parent_exists or context.parent_is_banned:
        return ValidationResult(valid=False)

    if not context.parent_is_processable:
        return ValidationResult(valid=False)

    if context.parent_author_name == comment.author_name:
        return ValidationResult(valid=False)

    comment_body = comment.body.lower()

    # Mod approval path: comment is a reply to a confirmation (non-root parent).
    if not context.parent_is_root:
        if "approved" in comment_body and context.is_moderator:
            if context.grandparent_exists and context.grandparent_is_root:
                return ValidationResult(
                    valid=True,
                    is_mod_approval=True,
                    parent_author=context.grandparent_author_name,
                    confirmer=context.parent_author_name,
                    parent_comment_id=context.grandparent_id,
                    reply_to_comment_id=context.parent_id,
                )
        return ValidationResult(valid=False)

    if not is_confirming_trade(comment_body):
        return ValidationResult(valid=False)

    if context.parent_is_saved:
        return ValidationResult(
            valid=False,
            reason="already_confirmed",
            parent_author=context.parent_author_name,
            parent_comment_id=context.parent_id,
        )

    username_lower = comment.author_name.lower()
    if (
        username_lower not in context.parent_body_lower
        and username_lower not in context.parent_body_html_lower
    ):
        return ValidationResult(
            valid=False,
            reason="cant_confirm_username",
            parent_author=context.parent_author_name,
            parent_body_lower=context.parent_body_lower,
            parent_body_html_lower=context.parent_body_html_lower,
            confirmer=username_lower,
        )

    return ValidationResult(
        valid=True,
        parent_author=context.parent_author_name,
        confirmer=comment.author_name,
        parent_comment_id=context.parent_id,
        reply_to_comment_id=comment.id,
    )


def find_flair_template(
    templates: dict[tuple[int, int], dict],
    trade_count: int,
    is_moderator: bool,
) -> dict | None:
    """Find the flair template matching the given trade count and mod status."""
    for (min_trades, max_trades), template in templates.items():
        if min_trades <= trade_count <= max_trades:
            if template["mod_only"] == is_moderator:
                return template
    return None
