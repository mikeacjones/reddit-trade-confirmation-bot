"""Comment-related activities for Temporal bot."""

import time

from temporalio import activity

from bot.reddit import (
    get_bot_user,
    get_reddit_client,
    get_subreddit,
    serialize_comment,
    should_process_redditor,
)

from ..shared import (
    CommentData,
    FetchCommentsInput,
    FetchCommentsResult,
    ReplyToCommentInput,
    ValidationResult,
    is_confirming_trade,
)
from .flair import is_moderator
from .helpers import TemplateManager

# Adaptive polling bounds (seconds)
_MIN_POLL_DELAY = 1.0
_MAX_POLL_DELAY = 3.0

# Alert when we scan deep into the listing and still cannot find the watermark.
_WATERMARK_GAP_SCAN_THRESHOLD = 900


@activity.defn
def poll_new_comments(input: FetchCommentsInput) -> FetchCommentsResult:
    """Long-running activity that polls for new comments.

    Runs an internal loop, heartbeating and sleeping between iterations.
    Returns only when actionable comments are found or a watermark gap
    is detected.  The workflow can cancel this activity via signals.

    Filters comments to only those on active submissions (current + previous
    month).  Root comments on the previous submission are included so the
    workflow can reject them with an "old thread" message.
    """
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)
    subreddit = get_subreddit(reddit)

    active_ids = set(input.active_submission_ids)
    current_submission_id = input.current_submission_id

    # Internal watermark tracking (evolves across poll iterations).
    original_seen_set = frozenset(input.seen_ids) if input.seen_ids else frozenset()
    seen_ids_set: set[str] = set(original_seen_set)
    had_initial_watermark = bool(original_seen_set)

    # IDs new relative to the workflow's watermark, newest-first.
    accumulated_new_ids: list[str] = []
    accumulated_new_set: set[str] = set()

    poll_delay = _MIN_POLL_DELAY

    while True:
        scanned_ids: list[str] = []  # ordered newest-first as we encounter them
        comments: list[CommentData] = []
        skipped_count = 0
        scanned_count = 0
        found_seen = not seen_ids_set  # if no known IDs, nothing to find
        batch_found_seen = False
        listing_exhausted = True

        for comment in subreddit.comments(limit=None):
            if (
                scanned_count % 100 == 0
            ):  # changing to 100 so we heartbeat on each page - under the hood, praw is paging 100 comments at a time
                activity.heartbeat(f"Scanned {scanned_count} comments")

            scanned_count += 1
            scanned_ids.append(comment.id)

            # Check if this comment is already known — either in our cache or saved.
            already_seen = comment.id in seen_ids_set or comment.saved
            if already_seen:
                batch_found_seen = True

            # At page boundaries (every 100 comments), decide whether to stop
            # after evaluating the current comment.
            should_stop_after_current = scanned_count % 100 == 0 and batch_found_seen

            if not already_seen:
                submission_id = comment.link_id[3:]
                if submission_id in active_ids:
                    if comment.banned_by is None and should_process_redditor(
                        comment.author, bot_user
                    ):
                        comment_body_lower = comment.body.lower()

                        if comment.is_root:
                            # Root comments on current submission are trade
                            # listings — skip. Root comments on previous
                            # submission are included so the workflow can
                            # reject them with an "old thread" message.
                            if submission_id != current_submission_id:
                                comments.append(serialize_comment(comment))
                        else:
                            if (
                                "confirmed" not in comment_body_lower
                                and "approved" not in comment_body_lower
                            ):
                                skipped_count += 1
                            else:
                                comments.append(serialize_comment(comment))

            if should_stop_after_current:
                found_seen = True
                listing_exhausted = False
                break

        if batch_found_seen:
            found_seen = True
            listing_exhausted = False

        # Update internal watermark with newly scanned IDs.
        seen_ids_set.update(scanned_ids)

        # Track IDs new relative to the workflow's original watermark.
        new_for_workflow = [
            sid
            for sid in scanned_ids
            if sid not in original_seen_set and sid not in accumulated_new_set
        ]
        accumulated_new_set.update(new_for_workflow)
        # Prepend: this iteration's IDs are newer than previous iterations'.
        accumulated_new_ids = new_for_workflow + accumulated_new_ids

        # Check for possible watermark gap.
        possible_gap = (
            had_initial_watermark
            and not found_seen
            and listing_exhausted
            and scanned_count >= _WATERMARK_GAP_SCAN_THRESHOLD
        )

        if comments or possible_gap:
            activity.logger.info(
                "Fetched %d comments for processing, skipped %d, "
                "scanned=%d, found_seen=%s, listing_exhausted=%s, possible_gap=%s",
                len(comments),
                skipped_count,
                scanned_count,
                found_seen,
                listing_exhausted,
                possible_gap,
            )
            return FetchCommentsResult(
                comments=comments,
                scanned_ids=accumulated_new_ids,
                found_seen=found_seen,
                listing_exhausted=listing_exhausted,
                scanned_count=scanned_count,
            )

        # No new comments and no gap — adaptive backoff and poll again.
        poll_delay = min(poll_delay * 2, _MAX_POLL_DELAY)
        time.sleep(poll_delay)


@activity.defn
def validate_confirmation(comment_data: CommentData) -> ValidationResult:
    """Validate a confirmation comment."""
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)

    # Root comments are filtered out by polling and should never reach here.
    if comment_data.is_root:
        return ValidationResult(valid=False)

    comment = reddit.comment(id=comment_data.id)
    subreddit = get_subreddit(reddit)

    # Get parent comment
    parent_comment = comment.parent()

    # Validate parent
    if parent_comment is None or parent_comment.banned_by is not None:
        return ValidationResult(valid=False)

    if not should_process_redditor(parent_comment.author, bot_user):
        return ValidationResult(valid=False)

    # Can't confirm your own trade
    if parent_comment.author.name == comment_data.author_name:
        return ValidationResult(valid=False)

    comment_body = comment_data.body.lower()

    # Handle moderator approval (for replies to confirmations)
    if not parent_comment.is_root:
        if "approved" in comment_body and is_moderator(
            comment_data.author_name, subreddit
        ):
            grandparent_comment = parent_comment.parent()
            if grandparent_comment and grandparent_comment.is_root:
                return ValidationResult(
                    valid=True,
                    is_mod_approval=True,
                    parent_author=grandparent_comment.author.name,
                    confirmer=parent_comment.author.name,
                    parent_comment_id=grandparent_comment.id,
                    reply_to_comment_id=parent_comment.id,
                )
        return ValidationResult(valid=False)

    # Check if this is a confirmation
    if not is_confirming_trade(comment_body):
        return ValidationResult(valid=False)

    # Check if already confirmed
    if parent_comment.saved:
        return ValidationResult(
            valid=False,
            reason="already_confirmed",
            parent_author=parent_comment.author.name,
            parent_comment_id=parent_comment.id,
        )

    # Verify user is mentioned in parent comment
    username_lower = comment_data.author_name.lower()
    parent_body_lower = parent_comment.body.lower()
    parent_html_lower = parent_comment.body_html.lower()

    if (
        username_lower not in parent_body_lower
        and username_lower not in parent_html_lower
    ):
        return ValidationResult(
            valid=False,
            reason="cant_confirm_username",
            parent_author=parent_comment.author.name,
        )

    # All checks passed
    return ValidationResult(
        valid=True,
        parent_author=parent_comment.author.name,
        confirmer=comment_data.author_name,
        parent_comment_id=parent_comment.id,
        reply_to_comment_id=comment_data.id,
    )


@activity.defn
def mark_comment_saved(comment_id: str) -> bool:
    """Mark a comment as saved (processed)."""
    reddit = get_reddit_client()
    comment = reddit.comment(id=comment_id)
    comment.save()
    return True


@activity.defn
def reply_to_comment(input: ReplyToCommentInput) -> str:
    """Reply to a comment using a template.

    Returns the reply comment ID.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)
    comment = reddit.comment(id=input.comment_id)

    if input.format_args:
        reply_text = TemplateManager.format(
            input.template_name, subreddit, **input.format_args
        )
    else:
        reply_text = TemplateManager.load(input.template_name, subreddit)

    reply = comment.reply(reply_text)
    if reply is None:
        raise RuntimeError("Confirmation reply failed to post")

    activity.logger.info("Replied to comment: https://reddit.com%s", reply.permalink)
    return reply.id
