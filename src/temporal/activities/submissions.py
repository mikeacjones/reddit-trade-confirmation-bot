"""Submission-related activities for Temporal bot."""

from datetime import datetime, timezone

from temporalio import activity

from ..shared import (
    LOGGER,
    SUBREDDIT_NAME,
    MONTHLY_POST_FLAIR_ID,
)
from .reddit import get_reddit_client, get_subreddit, get_bot_user
from .helpers import TemplateManager


@activity.defn
async def unsticky_previous_post() -> bool:
    """Unsticky the previous month's post."""
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)

    try:
        previous_submission = next(bot_user.submissions.new(limit=1))
    except StopIteration:
        LOGGER.info("No previous post to unsticky")
        return True

    if previous_submission.stickied:
        previous_submission.mod.sticky(state=False)
        LOGGER.info("Unstickied previous post: %s", previous_submission.permalink)

    return True


@activity.defn
async def create_monthly_post() -> str:
    """Create a new monthly confirmation thread.

    This activity is idempotent: if a post for the current month already exists,
    returns that submission ID without creating a duplicate.

    Returns the submission ID.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)
    bot_user = get_bot_user(reddit)

    now = datetime.now(timezone.utc)

    # Idempotency check: see if we already created a post this month
    try:
        last_post = next(bot_user.submissions.new(limit=1))
        post_date = datetime.fromtimestamp(last_post.created_utc, tz=timezone.utc)
        if post_date.year == now.year and post_date.month == now.month:
            LOGGER.info(
                "Monthly post already exists for this month (%s), returning existing ID (idempotent)",
                last_post.id,
            )
            return last_post.id
        previous_submission = last_post
    except StopIteration:
        previous_submission = None

    # Load templates
    post_template = TemplateManager.load("monthly_post", subreddit)
    title_template = TemplateManager.load("monthly_post_title", subreddit)

    LOGGER.info("Creating monthly post for r/%s", SUBREDDIT_NAME)

    # Create new post
    new_submission = subreddit.submit(
        title=now.strftime(title_template),
        selftext=post_template.format(
            bot_name=bot_user.name,
            subreddit_name=SUBREDDIT_NAME,
            previous_month_submission=previous_submission,
            now=now,
        ),
        flair_id=MONTHLY_POST_FLAIR_ID,
        send_replies=False,
    )

    # Configure new post
    new_submission.mod.sticky(bottom=False)
    new_submission.mod.suggested_sort(sort="new")

    LOGGER.info("Created monthly post: https://reddit.com%s", new_submission.permalink)
    return new_submission.id


@activity.defn
async def lock_previous_submissions() -> int:
    """Lock submissions from previous months.

    Returns the number of submissions locked.
    """
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)

    locked_count = 0
    for submission in bot_user.submissions.new(limit=10):
        if submission.stickied:
            continue
        if not submission.locked:
            submission.mod.lock()
            LOGGER.info("Locked: https://reddit.com%s", submission.permalink)
            locked_count += 1

    return locked_count
