"""Comment-related activities for Temporal bot."""

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from temporalio import activity

from ..shared import (
    ValidationResult,
    is_confirming_trade,
)
from .flair import FlairManager
from .helpers import TemplateManager
from .reddit import (
    get_bot_user,
    get_reddit_client,
    get_subreddit,
    serialize_comment,
    should_process_redditor,
)


@activity.defn
async def fetch_new_comments(
    last_seen_id: Optional[str] = None,
) -> list[dict]:
    """Fetch new comments from bot submissions across the subreddit.

    Returns list of serialized CommentData dicts for comments that need processing.
    Filters out and marks as saved comments that clearly don't need child workflows.
    Sends heartbeats during processing to signal liveness.
    """
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)
    subreddit = get_subreddit(reddit)

    activity.heartbeat("Fetching comments from subreddit")

    comments = []
    skipped_comments = []  # Comments to mark as saved without processing
    processed_count = 0

    for comment in subreddit.comments(limit=100):
        # Heartbeat every 10 comments to signal we're still alive
        processed_count += 1
        if processed_count % 10 == 0:
            activity.heartbeat(f"Processed {processed_count} comments")

        # Skip if we've already seen this comment
        # Reddit IDs are base36 - must convert to int for chronological comparison
        if last_seen_id and int(comment.id, 36) <= int(last_seen_id, 36):
            continue

        # Skip already processed
        if comment.saved:
            continue

        # Skip if not on a bot submission
        if comment.submission.author != bot_user:
            continue

        # Skip if submission is locked
        if comment.submission.locked:
            continue

        # Skip removed comments
        if comment.banned_by is not None:
            continue

        # Skip comments without valid authors (includes bot's own comments)
        if not should_process_redditor(comment.author, bot_user):
            continue

        comment_body_lower = comment.body.lower()

        # Check if this is the current stickied thread
        is_stickied = comment.submission.stickied

        # Filter logic to avoid unnecessary child workflows
        if comment.is_root:
            # Root comments in stickied (current) thread: skip entirely
            # DON'T mark as saved - saved flag on root comments indicates "trade confirmed"
            # Root comments in old thread: need processing for "old_confirmation_thread" reply
            if is_stickied:
                continue  # Skip but don't mark as saved
            # Old thread - still needs processing
        else:
            # Non-root comments: only process if they contain "confirmed" or "approved"
            if (
                "confirmed" not in comment_body_lower
                and "approved" not in comment_body_lower
            ):
                skipped_comments.append(comment)
                continue

        comments.append(asdict(serialize_comment(comment)))

    # Mark skipped comments as saved so they won't be fetched again
    for comment in skipped_comments:
        comment.save()

    activity.logger.info(
        "Fetched %d comments for processing, skipped %d from subreddit",
        len(comments),
        len(skipped_comments),
    )
    return comments


@activity.defn
async def validate_confirmation(comment_data: dict) -> dict:
    """Validate a confirmation comment.

    Returns ValidationResult as dict.
    """
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)
    subreddit = get_subreddit(reddit)
    comment = reddit.comment(id=comment_data["id"])

    # Top-level comments can't be confirmations
    if comment_data["is_root"]:
        # Check if this is in an old thread (non-stickied)
        submission = reddit.submission(id=comment_data["submission_id"])
        if not submission.stickied:
            comment_date = datetime.fromtimestamp(
                comment_data["created_utc"], tz=timezone.utc
            )
            now = datetime.now(timezone.utc)
            is_current_month = (
                comment_date.year == now.year and comment_date.month == now.month
            )
            if not is_current_month:
                return asdict(
                    ValidationResult(valid=False, reason="old_confirmation_thread")
                )

        return asdict(ValidationResult(valid=False))

    # Get parent comment
    parent_comment = comment.parent()

    # Validate parent
    if parent_comment is None or parent_comment.banned_by is not None:
        return asdict(ValidationResult(valid=False))

    if not should_process_redditor(parent_comment.author, bot_user):
        return asdict(ValidationResult(valid=False))

    # Can't confirm your own trade
    if parent_comment.author.name == comment_data["author_name"]:
        return asdict(ValidationResult(valid=False))

    comment_body = comment_data["body"].lower()

    # Handle moderator approval (for replies to confirmations)
    if not parent_comment.is_root:
        if "approved" in comment_body and FlairManager.is_moderator(
            comment_data["author_name"], subreddit
        ):
            grandparent_comment = parent_comment.parent()
            if grandparent_comment and grandparent_comment.is_root:
                return asdict(
                    ValidationResult(
                        valid=True,
                        is_mod_approval=True,
                        parent_author=grandparent_comment.author.name,
                        confirmer=parent_comment.author.name,
                        parent_comment_id=grandparent_comment.id,
                    )
                )
        return asdict(ValidationResult(valid=False))

    # Check if this is a confirmation
    if not is_confirming_trade(comment_body):
        return asdict(ValidationResult(valid=False))

    # Check if already confirmed
    if parent_comment.saved:
        return asdict(ValidationResult(valid=False, reason="already_confirmed"))

    # Verify user is mentioned in parent comment
    username_lower = comment_data["author_name"].lower()
    parent_body_lower = parent_comment.body.lower()
    parent_html_lower = parent_comment.body_html.lower()

    if (
        username_lower not in parent_body_lower
        and username_lower not in parent_html_lower
    ):
        return asdict(
            ValidationResult(
                valid=False,
                reason="cant_confirm_username",
                parent_author=parent_comment.author.name,
            )
        )

    # All checks passed
    return asdict(
        ValidationResult(
            valid=True,
            parent_author=parent_comment.author.name,
            confirmer=comment_data["author_name"],
            parent_comment_id=parent_comment.id,
        )
    )


@activity.defn
async def mark_comment_saved(comment_id: str) -> bool:
    """Mark a comment as saved (processed)."""
    reddit = get_reddit_client()
    comment = reddit.comment(id=comment_id)
    comment.save()
    return True


@activity.defn
async def reply_to_comment(
    comment_id: str,
    template_name: str,
    format_args: Optional[dict] = None,
) -> str:
    """Reply to a comment using a template.

    Returns the reply comment ID.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)
    comment = reddit.comment(id=comment_id)

    template = TemplateManager.load(template_name, subreddit)

    if format_args:
        reply_text = template.format(**format_args)
    else:
        reply_text = template

    reply = comment.reply(reply_text)
    if reply is None:
        raise RuntimeError("Confirmation reply failed to post")

    reply.save()
    activity.logger.info("Replied to comment: https://reddit.com%s", reply.permalink)
    return reply.id


@activity.defn
async def post_confirmation_reply(
    comment_id: str,
    parent_author: str,
    confirmer: str,
    parent_old_flair: Optional[str],
    parent_new_flair: Optional[str],
    confirmer_old_flair: Optional[str],
    confirmer_new_flair: Optional[str],
) -> str:
    """Post a trade confirmation reply.

    Returns the reply comment ID.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)
    comment = reddit.comment(id=comment_id)

    parent_comment = comment.parent()
    template = TemplateManager.load("trade_confirmation", subreddit)

    reply_text = template.format(
        comment=comment,
        parent_comment=parent_comment,
        old_parent_flair=parent_old_flair or "unknown",
        new_parent_flair=parent_new_flair or "unknown",
        old_comment_flair=confirmer_old_flair or "unknown",
        new_comment_flair=confirmer_new_flair or "unknown",
    )

    reply = comment.reply(reply_text)
    if reply is None:
        raise RuntimeError("Confirmation reply failed to post")

    reply.save()
    activity.logger.info("Trade confirmed: https://reddit.com%s", reply.permalink)
    return reply.id
