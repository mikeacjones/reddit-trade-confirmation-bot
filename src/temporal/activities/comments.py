"""Comment-related activities for Temporal bot."""

import time

from temporalio import activity

from bot.models import (
    CommentData,
    ConfirmationContext,
    FetchCommentsInput,
    FetchCommentsResult,
    ReplyToCommentInput,
    ValidationResult,
)
from bot.reddit import (
    get_bot_user,
    get_reddit_client,
    get_subreddit,
    serialize_comment,
    should_process_redditor,
)
from bot.rules import (
    classify_polled_comment,
    evaluate_confirmation,
    is_possible_watermark_gap,
)

from .flair import is_moderator
from .helpers import TemplateManager

# Adaptive polling bounds (seconds)
_MIN_POLL_DELAY = 1.0
_MAX_POLL_DELAY = 4.0

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

    # Watermark: IDs the workflow already knows about.
    watermark = frozenset(input.seen_ids) if input.seen_ids else frozenset()
    has_watermark = bool(watermark)

    # Known IDs: watermark + anything we scan across poll iterations.
    # Used to skip already-processed comments and detect overlap.
    known_ids: set[str] = set(watermark)

    # New IDs to return to the workflow (newest-first).
    # Built across poll iterations — each iteration prepends its batch.
    new_ids: list[str] = []
    new_ids_set: set[str] = set()

    poll_delay = _MIN_POLL_DELAY

    while True:
        comments: list[CommentData] = []
        skipped_count = 0
        scanned_count = 0
        hit_known = False
        stopped_early = False
        batch_new_ids: list[str] = []

        for comment in subreddit.comments(limit=None):
            # Heartbeat at page boundaries. PRAW fetches 100 comments per
            # API call before yielding, so the first page is already loaded
            # when we enter the loop (scanned_count == 0).
            if scanned_count % 100 == 0:
                activity.heartbeat(f"Scanned {scanned_count} comments")

            scanned_count += 1

            if comment.id in known_ids or comment.saved:
                hit_known = True
            else:
                known_ids.add(comment.id)
                batch_new_ids.append(comment.id)

                # link_id is "t3_abc123"; strip prefix to get raw submission ID
                # without triggering a lazy submission load.
                submission_id = comment.link_id[3:]
                if submission_id in active_ids:
                    if comment.banned_by is None and should_process_redditor(
                        comment.author, bot_user
                    ):
                        action = classify_polled_comment(
                            submission_id=submission_id,
                            current_submission_id=current_submission_id,
                            is_root=comment.is_root,
                            body_lower=comment.body.lower(),
                        )
                        if action == "include":
                            comments.append(serialize_comment(comment))
                        elif action == "skip_irrelevant":
                            skipped_count += 1

            # At page boundaries, stop if we've overlapped with known comments.
            if scanned_count % 100 == 0 and hit_known:
                stopped_early = True
                break

        # Prepend this batch's new IDs (newest-first within batch,
        # and newer batches go in front of older ones).
        for sid in batch_new_ids:
            if sid not in new_ids_set:
                new_ids_set.add(sid)
        new_ids = batch_new_ids + new_ids

        found_seen = hit_known or not has_watermark
        listing_exhausted = not stopped_early

        # Check for possible watermark gap.
        possible_gap = is_possible_watermark_gap(
            had_initial_watermark=has_watermark,
            found_seen=found_seen,
            listing_exhausted=listing_exhausted,
            scanned_count=scanned_count,
            gap_threshold=_WATERMARK_GAP_SCAN_THRESHOLD,
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
                scanned_ids=new_ids,
                found_seen=found_seen,
                listing_exhausted=listing_exhausted,
                scanned_count=scanned_count,
            )

        # No new comments and no gap — adaptive backoff and poll again.
        poll_delay = min(poll_delay * 2, _MAX_POLL_DELAY)
        time.sleep(poll_delay)


@activity.defn
def validate_confirmation(comment_data: CommentData) -> ValidationResult:
    """Fetch parent/grandparent data and delegate to pure validation rules."""
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)
    subreddit = get_subreddit(reddit)

    comment = reddit.comment(id=comment_data.id)
    parent = comment.parent()

    context = ConfirmationContext(parent_exists=parent is not None)

    if parent is not None:
        context.parent_is_banned = parent.banned_by is not None
        context.parent_is_processable = (
            not context.parent_is_banned
            and should_process_redditor(parent.author, bot_user)
        )

        if context.parent_is_processable:
            context.parent_author_name = str(parent.author.name)
            context.parent_id = str(parent.id)
            context.parent_is_root = bool(parent.is_root)
            context.parent_is_saved = bool(parent.saved)
            context.parent_body_lower = str(parent.body).lower()
            context.parent_body_html_lower = str(parent.body_html).lower()
            context.is_moderator = is_moderator(comment_data.author_name, subreddit)

            if not parent.is_root:
                grandparent = parent.parent()
                if grandparent and hasattr(grandparent, "is_root"):
                    context.grandparent_exists = True
                    context.grandparent_is_root = bool(grandparent.is_root)
                    context.grandparent_author_name = (
                        str(grandparent.author.name) if grandparent.author else ""
                    )
                    context.grandparent_id = str(grandparent.id)

    return evaluate_confirmation(comment_data, context)


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
