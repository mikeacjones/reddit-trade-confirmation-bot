"""Comment-related activities for Temporal bot."""

from dataclasses import asdict
from types import SimpleNamespace
from typing import Optional

from temporalio import activity

from ..shared import (
    ValidationResult,
    is_confirming_trade,
)
from .flair import FlairManager
from .helpers import TemplateManager
from .reddit import (
    get_bot_submissions,
    get_bot_user,
    get_reddit_client,
    get_subreddit,
    serialize_comment,
    should_process_redditor,
)


@activity.defn
async def fetch_new_comments(
    last_seen_id: Optional[str] = None,
) -> dict:
    """Fetch new comments from bot submissions across the subreddit.

    Returns:
        dict with:
            - comments: list of serialized CommentData dicts to process
            - newest_seen_id: newest comment ID observed during this poll
            - watermark_found: whether we reached `last_seen_id` while scanning
            - listing_exhausted: whether we consumed the full listing window
            - scanned_count: number of comments scanned in this poll

    Filters out and marks as saved comments that clearly don't need child workflows.
    Sends heartbeats during processing to signal liveness.
    """
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)
    bot_username = bot_user.name
    subreddit = get_subreddit(reddit)

    activity.heartbeat("Fetching bot submissions")

    # Cache bot submission info to avoid lazy-loading each comment's submission
    bot_submissions = get_bot_submissions(reddit, limit=10)
    bot_submission_ids = set(bot_submissions.keys())

    activity.heartbeat("Fetching comments from subreddit")

    comments = []
    skipped_count = 0
    scanned_count = 0
    newest_seen_id = last_seen_id
    newest_seen_value = int(last_seen_id, 36) if last_seen_id else None
    last_seen_value = int(last_seen_id, 36) if last_seen_id else None
    watermark_found = last_seen_id is None
    listing_exhausted = True

    # PRAW paginates listing requests under the hood (100 per API call).
    # We iterate until we reach last_seen_id to avoid missing bursts >100 comments.
    for comment in subreddit.comments(limit=None):
        # Heartbeat every 50 comments to signal we're still alive.
        scanned_count += 1
        if scanned_count % 50 == 0:
            activity.heartbeat(f"Scanned {scanned_count} comments")

        comment_id_value = int(comment.id, 36)

        # Results are newest-first; once we reach the watermark, older items follow.
        if last_seen_value is not None and comment_id_value <= last_seen_value:
            watermark_found = True
            listing_exhausted = False
            break

        # Track the newest comment seen, even if we skip processing it.
        if newest_seen_value is None or comment_id_value > newest_seen_value:
            newest_seen_id = comment.id
            newest_seen_value = comment_id_value

        # Skip already processed
        if comment.saved:
            continue

        # Skip if not on a bot submission (use link_id to avoid lazy loading)
        # link_id is in format "t3_xxxxx", strip the "t3_" prefix to get submission ID
        submission_id = comment.link_id[3:]
        if submission_id not in bot_submission_ids:
            continue

        # Use cached submission to avoid lazy-loading
        submission = bot_submissions[submission_id]

        # Skip if submission is locked
        if submission.locked:
            continue

        # Skip removed comments
        if comment.banned_by is not None:
            continue

        # Skip comments without valid authors (includes bot's own comments)
        if not should_process_redditor(comment.author, bot_username):
            continue

        comment_body_lower = comment.body.lower()

        # Check if this is the current stickied thread (use cached submission)
        is_stickied = submission.stickied

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
                comment.save()
                skipped_count += 1
                continue

        comments.append(asdict(serialize_comment(comment)))

    activity.logger.info(
        (
            "Fetched %d comments for processing, skipped %d from subreddit, "
            "scanned=%d, newest_seen_id=%s, watermark_found=%s, listing_exhausted=%s"
        ),
        len(comments),
        skipped_count,
        scanned_count,
        newest_seen_id,
        watermark_found,
        listing_exhausted,
    )
    return {
        "comments": comments,
        "newest_seen_id": newest_seen_id,
        "watermark_found": watermark_found,
        "listing_exhausted": listing_exhausted,
        "scanned_count": scanned_count,
    }


@activity.defn
async def validate_confirmation(comment_data: dict) -> dict:
    """Validate a confirmation comment.

    Returns ValidationResult as dict.
    """
    reddit = get_reddit_client()
    bot_username = get_bot_user(reddit).name
    subreddit = get_subreddit(reddit)

    # Top-level comments can't be confirmations
    if comment_data["is_root"]:
        # Root comments can only start new trades on the current stickied thread.
        # On non-stickied threads, reject as old_confirmation_thread so the workflow
        # can reply with guidance and lock the initiating comment.
        submission = reddit.submission(id=comment_data["submission_id"])
        if not submission.stickied:
            return asdict(
                ValidationResult(valid=False, reason="old_confirmation_thread")
            )

        return asdict(ValidationResult(valid=False))

    # Get parent comment directly from serialized parent fullname to avoid
    # fetching the child comment only to resolve parent().
    parent_fullname = comment_data.get("parent_id", "")
    if not isinstance(parent_fullname, str) or not parent_fullname.startswith("t1_"):
        return asdict(ValidationResult(valid=False))
    parent_comment = reddit.comment(id=parent_fullname[3:])

    # Validate parent
    if parent_comment is None or parent_comment.banned_by is not None:
        return asdict(ValidationResult(valid=False))

    if not should_process_redditor(parent_comment.author, bot_username):
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
                # Use the root trade comment saved flag for dedupe. This prevents
                # repeated mod approvals on the same trade from re-incrementing.
                if grandparent_comment.saved:
                    return asdict(
                        ValidationResult(valid=False, reason="already_confirmed")
                    )
                if not should_process_redditor(
                    grandparent_comment.author, bot_username
                ):
                    return asdict(ValidationResult(valid=False))
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
async def lock_comment(comment_id: str) -> bool:
    """Lock a comment to prevent new replies."""
    reddit = get_reddit_client()
    comment = reddit.comment(id=comment_id)
    comment.mod.lock()
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

    template = TemplateManager.load("trade_confirmation", subreddit)
    comment_context = SimpleNamespace(author=SimpleNamespace(name=confirmer))
    parent_comment_context = SimpleNamespace(author=SimpleNamespace(name=parent_author))

    reply_text = template.format(
        confirmer=confirmer,
        parent_author=parent_author,
        comment=comment_context,
        parent_comment=parent_comment_context,
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
