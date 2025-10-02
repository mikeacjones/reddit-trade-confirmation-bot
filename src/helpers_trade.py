"""Trade confirmation logic helpers."""
from datetime import datetime
from praw import models
import prawcore.exceptions
from logger import LOGGER
from helpers_redditor import should_process_redditor

def should_process_comment(comment: models.Comment, settings) -> bool:
    """Checks if we should actually process a comment in our stream loop."""
    return (
        not comment.saved
        and comment.banned_by is None
        and comment.submission
        and should_process_redditor(comment.author, settings)
    )


def is_confirming_trade(comment_body: str) -> bool:
    """Checks if the message is confirming a trade, returns a boolean."""
    return "confirmed" in comment_body.lower()


def handle_automoderator_comment(comment: models.Comment, settings) -> None:
    """Handles a comment left by AutoModerator."""
    if "removed" in comment.body.lower():
        comment.submission.mod.remove()
        LOGGER.info(
            "AutoModerator removed https://reddit.com%s",
            comment.submission.permalink,
        )
        comment.save()


def handle_non_confirmation_thread(comment: models.Comment, settings) -> None:
    """Handles a comment left outside the confirmation thread."""
    from helpers_flair import set_flair
    
    if not comment.author_flair_text or comment.author_flair_text == "":
        set_flair(comment.author.name, 0, settings)


def handle_root_confirmation_thread(comment: models.Comment, settings) -> None:
    """Handles a root level comment on a confirmation thread."""
    if comment.submission.stickied:
        return

    comment_datetime = datetime.utcfromtimestamp(comment.created_utc)
    now = datetime.utcnow()
    is_same_month_year = (
        comment_datetime.year == now.year and comment_datetime.month == now.month
    )
    if not is_same_month_year:
        return

    comment.mod.lock()
    comment.reply(settings.OLD_CONFIRMATION_THREAD.format(comment=comment))
    comment.save()


def handle_confirmation_thread(comment: models.Comment, settings) -> None:
    """Handles a comment left on the confirmation thread."""
    from helpers_flair import increment_trades
    
    if comment.is_root:
        handle_root_confirmation_thread(comment, settings)
        return

    parent_comment = comment.parent()

    if (
        not parent_comment
        or parent_comment.banned_by is not None
        or not should_process_redditor(parent_comment.author, settings)
        or parent_comment.author == comment.author
    ):
        comment.save()
        return

    comment_body = comment.body.lower()

    # Check for mod approval of disputed trade
    if not parent_comment.is_root:
        if "approved" in comment_body and comment.author.name in settings.CURRENT_MODS:
            parent_parent_comment = parent_comment.parent()
            if not parent_parent_comment.is_root:
                return
            increment_trades(parent_parent_comment, parent_comment, settings)
            comment.save()
            return
        comment.save()
        return

    # Check if confirming trade
    if not is_confirming_trade(comment_body):
        comment.save()
        return

    # Check if already confirmed
    if parent_comment.saved:
        comment.reply(
            settings.ALREADY_CONFIRMED_TEMPLATE.format(
                comment=comment, parent_comment=parent_comment
            )
        )
        LOGGER.info(
            "u/%s attempted to confirm already confirmed trade at https://reddit.com%s",
            comment.author.name,
            comment.permalink,
        )
        comment.save()
        return

    # Check if username is mentioned
    if (
        comment.author.name.lower() not in parent_comment.body.lower()
        and comment.author.name.lower() not in parent_comment.body_html.lower()
    ):
        comment.save()
        comment.reply(
            settings.CANT_CONFIRM_USERNAME_TEMPLATE.format(
                comment=comment, parent_comment=parent_comment
            )
        )
        LOGGER.info(
            "u/%s attempted to confirm trade where they were not specified. https://reddit.com%s",
            comment.author.name,
            parent_comment.permalink,
        )
        return

    LOGGER.info(
        "Found a trade that needs to be confirmed: https://reddit.com%s",
        comment.permalink,
    )

    increment_trades(parent_comment, comment, settings)


def handle_catch_up(settings) -> None:
    """Processes any comments that were missed during downtime."""
    current_submission = next(settings.ME.submissions.new(limit=1))
    current_submission.comment_sort = "new"
    current_submission.comments.replace_more(limit=None)
    LOGGER.info("Starting catch-up process")
    for comment in current_submission.comments.list():
        if comment.saved:
            continue
        try:
            handle_confirmation_thread(comment, settings)
        except Exception as ex:
            LOGGER.exception("Error during catch-up: %s", ex)
    LOGGER.info("Catch-up process finished")
