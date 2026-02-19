"""Submission-related activities for Temporal bot."""

from datetime import datetime, timezone
from types import SimpleNamespace

from temporalio import activity

from ..shared import (
    MONTHLY_POST_FLAIR_ID,
    SUBREDDIT_NAME,
)
from .helpers import TemplateManager
from .reddit import get_bot_user, get_reddit_client, get_subreddit


@activity.defn
def unsticky_previous_post() -> dict | None:
    """Unsticky the previous month's post."""
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)

    try:
        previous_submission = next(bot_user.submissions.new(limit=1))
    except StopIteration:
        activity.logger.info("No previous post to unsticky")
        return None

    if previous_submission.stickied:
        previous_submission.mod.sticky(state=False)
        activity.logger.info(
            "Unstickied previous post: %s", previous_submission.permalink
        )

    return {
        "id": previous_submission.id,
        "title": previous_submission.title,
        "permalink": previous_submission.permalink,
        "created_utc": previous_submission.created_utc,
    }


@activity.defn
def create_monthly_post(previous_submission_data: dict | None = None) -> str:
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
    previous_submission = previous_submission_data
    if previous_submission is None:
        try:
            last_post = next(bot_user.submissions.new(limit=1))
            previous_submission = {
                "id": last_post.id,
                "title": last_post.title,
                "permalink": last_post.permalink,
                "created_utc": last_post.created_utc,
            }
        except StopIteration:
            previous_submission = None

    if previous_submission is not None:
        post_date = datetime.fromtimestamp(previous_submission["created_utc"], tz=timezone.utc)
        if post_date.year == now.year and post_date.month == now.month:
            activity.logger.info(
                "Monthly post already exists for this month (%s), returning existing ID (idempotent)",
                previous_submission["id"],
            )
            return previous_submission["id"]

    if previous_submission:
        template_submission = SimpleNamespace(
            title=previous_submission["title"],
            permalink=previous_submission["permalink"],
        )
    else:
        template_submission = SimpleNamespace(
            title="Previous monthly thread",
            permalink=f"https://www.reddit.com/r/{SUBREDDIT_NAME}/",
        )

    # Load templates
    post_template = TemplateManager.load("monthly_post", subreddit)
    title_template = TemplateManager.load("monthly_post_title", subreddit)

    activity.logger.info("Creating monthly post for r/%s", SUBREDDIT_NAME)

    # Create new post
    new_submission = subreddit.submit(
        title=now.strftime(title_template),
        selftext=post_template.format(
            bot_name=bot_user.name,
            subreddit_name=SUBREDDIT_NAME,
            previous_month_submission=template_submission,
            now=now,
        ),
        flair_id=MONTHLY_POST_FLAIR_ID,
        send_replies=False,
    )

    # Configure new post
    new_submission.mod.sticky(bottom=False)
    new_submission.mod.suggested_sort(sort="new")

    activity.logger.info(
        "Created monthly post: https://reddit.com%s", new_submission.permalink
    )
    return new_submission.id


@activity.defn
def lock_previous_submissions() -> int:
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
            activity.logger.info("Locked: https://reddit.com%s", submission.permalink)
            locked_count += 1

    return locked_count
