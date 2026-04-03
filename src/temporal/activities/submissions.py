"""Submission-related activities for Temporal bot."""

from datetime import datetime, timezone
from types import SimpleNamespace

from temporalio import activity

from bot.config import MONTHLY_POST_FLAIR_ID, SUBREDDIT_NAME
from bot.models import ActiveSubmissions, CreateMonthlyPostInput, SubmissionInput
from bot.reddit import get_bot_user, get_reddit_client, get_subreddit
from .helpers import TemplateManager


@activity.defn
def fetch_active_submission_ids() -> ActiveSubmissions:
    """Discover current and previous submission IDs from Reddit.

    Used once at polling workflow startup to bootstrap state.
    Stickied submission = current; most recent non-stickied, non-locked = previous.
    """
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)

    current_id: str | None = None
    previous_id: str | None = None

    for submission in bot_user.submissions.new(limit=5):
        if submission.stickied and current_id is None:
            current_id = submission.id
        elif not submission.stickied and not submission.locked and previous_id is None:
            previous_id = submission.id

        if current_id is not None and previous_id is not None:
            break

    activity.logger.info(
        "Discovered submissions: current=%s, previous=%s", current_id, previous_id
    )
    return ActiveSubmissions(
        current_submission_id=current_id,
        previous_submission_id=previous_id,
    )


@activity.defn
def sticky_submission(input: SubmissionInput) -> bool:
    """Sticky a specific submission. Idempotent."""
    reddit = get_reddit_client()
    submission = reddit.submission(id=input.submission_id)

    if not submission.stickied:
        submission.mod.sticky(bottom=False)
        activity.logger.info(
            "Stickied: https://reddit.com%s", submission.permalink
        )

    return True


@activity.defn
def unsticky_submission(input: SubmissionInput) -> bool:
    """Unsticky a specific submission. Idempotent."""
    reddit = get_reddit_client()
    submission = reddit.submission(id=input.submission_id)

    if submission.stickied:
        submission.mod.sticky(state=False)
        activity.logger.info(
            "Unstickied: https://reddit.com%s", submission.permalink
        )

    return True


@activity.defn
def lock_submission(input: SubmissionInput) -> bool:
    """Lock a specific submission. Idempotent."""
    reddit = get_reddit_client()
    submission = reddit.submission(id=input.submission_id)

    if not submission.locked:
        submission.mod.lock()
        activity.logger.info(
            "Locked: https://reddit.com%s", submission.permalink
        )

    return True


@activity.defn
def create_monthly_post(input: CreateMonthlyPostInput) -> str:
    """Create a new monthly confirmation thread.

    This activity is idempotent: if a post for the current month already exists,
    returns that submission ID without creating a duplicate.

    No longer stickies the post — that is handled by a separate activity.

    Returns the submission ID.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)
    bot_user = get_bot_user(reddit)

    now = datetime.now(timezone.utc)

    # Fetch previous submission metadata for idempotency check and template context.
    previous_submission: dict | None = None
    if input.previous_submission_id:
        sub = reddit.submission(id=input.previous_submission_id)
        previous_submission = {
            "id": sub.id,
            "title": sub.title,
            "permalink": sub.permalink,
            "created_utc": sub.created_utc,
        }
    else:
        try:
            last_post = next(bot_user.submissions.new(limit=1))
            previous_submission = {
                "id": last_post.id,
                "title": last_post.title,
                "permalink": last_post.permalink,
                "created_utc": last_post.created_utc,
            }
        except StopIteration:
            pass

    # Idempotency check: see if we already created a post this month
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

    new_submission.mod.suggested_sort(sort="new")

    activity.logger.info(
        "Created monthly post: https://reddit.com%s", new_submission.permalink
    )
    return new_submission.id
