"""Trade confirmation bot v2.0 for Reddit using praw-bot-wrapper"""
import os
import sys
import praw_bot_wrapper
from datetime import datetime
from praw import models, Reddit
from pushover import Pushover
from logger import LOGGER
from settings import Settings
from helpers import load_secrets
from helpers_submission import lock_previous_submissions, post_monthly_submission
from helpers_trade import (
    should_process_comment,
    handle_automoderator_comment,
    handle_non_confirmation_thread,
    handle_confirmation_thread,
    handle_catch_up
)

SUBREDDIT_NAME = os.environ["SUBREDDIT_NAME"]
SECRETS = load_secrets(SUBREDDIT_NAME)
PUSHOVER = Pushover(SECRETS["PUSHOVER_APP_TOKEN"], SECRETS["PUSHOVER_USER_TOKEN"])
BOT = Reddit(
    client_id=SECRETS["REDDIT_CLIENT_ID"],
    client_secret=SECRETS["REDDIT_CLIENT_SECRET"],
    user_agent=SECRETS["REDDIT_USER_AGENT"],
    username=SECRETS["REDDIT_USERNAME"],
    password=SECRETS["REDDIT_PASSWORD"],
)
SETTINGS = Settings(BOT, SUBREDDIT_NAME)


@praw_bot_wrapper.stream_handler(SETTINGS.SUBREDDIT.stream.comments)
def handle_new_comment(comment: models.Comment) -> None:
    """Handles all new comments in the subreddit."""
    if not should_process_comment(comment, SETTINGS):
        return

    LOGGER.info("Processing new comment https://reddit.com%s", comment.permalink)

    # Handle AutoModerator comments
    if comment.author.name.lower() == "automoderator":
        handle_automoderator_comment(comment, SETTINGS)
        return

    # Handle comments outside confirmation thread
    if comment.submission.author != SETTINGS.ME:
        handle_non_confirmation_thread(comment, SETTINGS)
        return

    # Handle comments on confirmation thread
    if comment.submission.author == SETTINGS.ME:
        handle_confirmation_thread(comment, SETTINGS)
        return


@praw_bot_wrapper.stream_handler(BOT.inbox.stream)
def handle_inbox(message: models.Message | models.Comment | models.Submission) -> None:
    """Monitors messages sent to the bot."""
    message.mark_read()
    if (
        not isinstance(message, models.Message)
        or message.author not in SETTINGS.CURRENT_MODS
    ):
        return
    
    message_lower = message.body.lower()
    
    if "reload" in message_lower:
        LOGGER.info("Mod requested settings reload")
        SETTINGS.reload(BOT, SUBREDDIT_NAME)
        message.reply("Successfully reloaded bot settings")
    
    message.mark_read()


@praw_bot_wrapper.outage_recovery_handler(outage_threshold=10)
def notify_outage_recovery(started_at: datetime) -> None:
    """Handles recovery from extended Reddit API outages."""
    LOGGER.info("Bot recovered from extended outage that started at %s", started_at)
    
    # Send modmail notification
    SETTINGS.SUBREDDIT.modmail.create(
        subject="Bot Recovered from Extended Outage",
        body=f"The trade confirmation bot has recovered from an extended outage that started at {started_at}.\n\nRunning catch-up process to handle any missed comments.",
        recipient=SETTINGS.ME,
    )
    
    # Send Pushover notification
    PUSHOVER.send_message(
        f"Bot error for r/{SUBREDDIT_NAME} - Recovered from Reddit API outage",
        title="Bot Recovery"
    )
    
    # Run catch-up process
    handle_catch_up(SETTINGS)


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            if sys.argv[1] == "create-monthly":
                post_monthly_submission(SETTINGS, PUSHOVER)
            elif sys.argv[1] == "lock-submissions":
                PUSHOVER.send_message(
                    f"Locking previous month's posts for r/{SUBREDDIT_NAME}"
                )
                lock_previous_submissions(SETTINGS)
        else:
            LOGGER.info("Bot start up")
            PUSHOVER.send_message(f"Bot startup for r/{SUBREDDIT_NAME}")
            
            # Run initial catch-up
            handle_catch_up(SETTINGS)
            
            # Start the bot
            praw_bot_wrapper.run()
    except Exception as main_exception:
        LOGGER.exception("Main crashed")
        PUSHOVER.send_message(
            f"Bot error for r/{SUBREDDIT_NAME}",
            title="Bot Crash"
        )
        PUSHOVER.send_message(str(main_exception))
        raise
