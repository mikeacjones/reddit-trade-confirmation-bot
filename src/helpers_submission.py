"""Submission-related helper functions."""
from datetime import datetime
from logger import LOGGER
from helpers_template import load_template


def post_monthly_submission(settings, pushover) -> None:
    """Creates the monthly confirmation thread."""
    previous_submission = next(settings.ME.submissions.new(limit=1))
    submission_datetime = datetime.utcfromtimestamp(previous_submission.created_utc)
    now = datetime.utcnow()
    is_same_month_year = (
        submission_datetime.year == now.year and submission_datetime.month == now.month
    )
    if is_same_month_year:
        LOGGER.info(
            "Post monthly confirmation called and skipped; monthly post already exists"
        )
        return

    monthly_post_template = load_template(settings.SUBREDDIT, "monthly_post")
    monthly_post_title_template = load_template(settings.SUBREDDIT, "monthly_post_title")
    pushover.send_message(f"Creating monthly post for r/{settings.SUBREDDIT_NAME}")

    if previous_submission.stickied:
        previous_submission.mod.sticky(state=False)

    new_submission = settings.SUBREDDIT.submit(
        title=now.strftime(monthly_post_title_template),
        selftext=monthly_post_template.format(
            bot_name=settings.BOT_NAME,
            subreddit_name=settings.SUBREDDIT_NAME,
            previous_month_submission=previous_submission,
            now=now,
        ),
        flair_id=settings.MONTHLY_POST_FLAIR_ID,
        send_replies=False,
    )
    new_submission.mod.sticky(bottom=False)
    new_submission.mod.suggested_sort(sort="new")
    LOGGER.info(
        "Created new monthly confirmation post: https://reddit.com%s",
        new_submission.permalink,
    )


def lock_previous_submissions(settings) -> None:
    """Locks previous month posts."""
    LOGGER.info("Locking previous submissions")
    for submission in settings.ME.submissions.new(limit=10):
        if submission.stickied:
            continue
        if not submission.locked:
            LOGGER.info("Locking https://reddit.com%s", submission.permalink)
            submission.mod.lock()


def get_current_confirmation_post(settings):
    """Gets the current month's confirmation post."""
    return next(settings.ME.submissions.new(limit=1))
