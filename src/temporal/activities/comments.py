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
def fetch_new_comments(
    last_seen_id: Optional[str] = None,
) -> dict:
    """Fetch new comments from bot submissions across the subreddit.

    Returns:
        dict with:
            - comments: list of serialized CommentData dicts to process
            - newest_seen_id: newest comment ID observed during this poll
            - watermark_found: whether we reached `last_seen_id` while scanning
            - listing_exhausted: whether we consumed the listing window
            - scanned_count: number of comments scanned in this poll

    Filters out comments that clearly don't need workflows.
    Sends heartbeats during processing to signal liveness.
    """
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)
    subreddit = get_subreddit(reddit)

    activity.heartbeat("Fetching bot submissions")

    # Cache bot submission info to avoid lazy-loading each comment's submission
    bot_submissions = {s.id: s for s in bot_user.submissions.new(limit=10)}
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

    # Iterate until we find the watermark comment to avoid missing bursts.
    for comment in subreddit.comments(limit=None):
        scanned_count += 1
        if scanned_count % 50 == 0:
            activity.heartbeat(f"Scanned {scanned_count} comments")

        comment_id_value = int(comment.id, 36)

        # Results are newest-first; once we reach watermark, remaining are older.
        if last_seen_value is not None and comment_id_value <= last_seen_value:
            watermark_found = True
            listing_exhausted = False
            break

        # Track newest observed comment even when filtered from processing.
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
        if not should_process_redditor(comment.author, bot_user):
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
                skipped_count += 1
                continue

        serialized_comment = asdict(serialize_comment(comment))
        serialized_comment["submission_stickied"] = is_stickied
        comments.append(serialized_comment)

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
def validate_confirmation(comment_data: dict) -> dict:
    """Validate a confirmation comment.

    Returns ValidationResult as dict.
    """
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)

    # Top-level comments can't be confirmations
    if comment_data["is_root"]:
        # Polling includes cached stickied state; fall back to direct fetch if absent.
        submission_stickied = comment_data.get("submission_stickied")
        if submission_stickied is None:
            submission = reddit.submission(id=comment_data["submission_id"])
            submission_stickied = submission.stickied

        if submission_stickied is False:
            comment_date = datetime.fromtimestamp(comment_data["created_utc"], tz=timezone.utc)
            now = datetime.now(timezone.utc)
            is_current_month = comment_date.year == now.year and comment_date.month == now.month
            if not is_current_month:
                return asdict(ValidationResult(valid=False, reason="old_confirmation_thread"))

        return asdict(ValidationResult(valid=False))

    comment = reddit.comment(id=comment_data["id"])
    subreddit = get_subreddit(reddit)

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
                        reply_to_comment_id=parent_comment.id,
                    )
                )
        return asdict(ValidationResult(valid=False))

    # Check if this is a confirmation
    if not is_confirming_trade(comment_body):
        return asdict(ValidationResult(valid=False))

    # Check if already confirmed
    if parent_comment.saved:
        return asdict(
            ValidationResult(
                valid=False,
                reason="already_confirmed",
                parent_author=parent_comment.author.name,
                parent_comment_id=parent_comment.id,
            )
        )

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
            reply_to_comment_id=comment_data["id"],
        )
    )


@activity.defn
def mark_comment_saved(comment_id: str) -> bool:
    """Mark a comment as saved (processed)."""
    reddit = get_reddit_client()
    comment = reddit.comment(id=comment_id)
    comment.save()
    return True


@activity.defn
def reply_to_comment(
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
        try:
            reply_text = template.format(**format_args)
        except (KeyError, ValueError, IndexError) as exc:
            activity.logger.warning(
                "Template '%s' formatting failed (%s: %s); falling back to local file",
                template_name, type(exc).__name__, exc,
            )
            local_template = TemplateManager.load_local(template_name)
            TemplateManager._cache[template_name] = local_template
            reply_text = local_template.format(**format_args)
    else:
        reply_text = template

    reply = comment.reply(reply_text)
    if reply is None:
        raise RuntimeError("Confirmation reply failed to post")

    activity.logger.info("Replied to comment: https://reddit.com%s", reply.permalink)
    return reply.id


@activity.defn
def post_confirmation_reply(
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
    # Build a flat dict of all available data — no PRAW objects or lazy-loadable objects.
    format_args = {
        "comment_id": comment_id,
        "confirmer": confirmer,
        "parent_author": parent_author,
        "old_comment_flair": confirmer_old_flair or "unknown",
        "new_comment_flair": confirmer_new_flair or "unknown",
        "old_parent_flair": parent_old_flair or "unknown",
        "new_parent_flair": parent_new_flair or "unknown",
    }
    try:
        reply_text = template.format(**format_args)
    except (KeyError, ValueError, IndexError) as exc:
        activity.logger.warning(
            "Template 'trade_confirmation' formatting failed (%s: %s); falling back to local file",
            type(exc).__name__, exc,
        )
        local_template = TemplateManager.load_local("trade_confirmation")
        TemplateManager._cache["trade_confirmation"] = local_template
        reply_text = local_template.format(**format_args)

    reply = comment.reply(reply_text)
    if reply is None:
        raise RuntimeError("Confirmation reply failed to post")

    activity.logger.info("Trade confirmed: https://reddit.com%s", reply.permalink)
    return reply.id
