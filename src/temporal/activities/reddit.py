"""Reddit client utilities for activities.

This module contains praw-related code that can only be used in activities,
not in workflows (due to Temporal sandbox restrictions).
"""

import praw

from ..shared import SECRETS, SUBREDDIT_NAME, CommentData


def get_reddit_client() -> praw.Reddit:
    """Create a fresh Reddit client instance."""
    return praw.Reddit(
        client_id=SECRETS["REDDIT_CLIENT_ID"],
        client_secret=SECRETS["REDDIT_CLIENT_SECRET"],
        user_agent=SECRETS["REDDIT_USER_AGENT"],
        username=SECRETS["REDDIT_USERNAME"],
        password=SECRETS["REDDIT_PASSWORD"],
    )


def get_subreddit(reddit: praw.Reddit) -> praw.models.Subreddit:
    """Get the configured subreddit."""
    return reddit.subreddit(SUBREDDIT_NAME)


def get_bot_user(reddit: praw.Reddit):
    """Get the bot user."""
    return reddit.user.me()


def should_process_redditor(redditor, bot_user) -> bool:
    """Check if redditor should be processed.

    Args:
        redditor: The redditor to check.
        bot_user: The bot's user object (to avoid processing bot's own comments).
    """
    if redditor is None:
        return False
    if not hasattr(redditor, "id"):
        return False
    if redditor.id == bot_user.id:
        return False
    if hasattr(redditor, "is_suspended") and redditor.is_suspended:
        return False
    return True


def serialize_comment(comment: praw.models.Comment) -> CommentData:
    """Convert a PRAW comment to serializable data."""
    return CommentData(
        id=comment.id,
        body=comment.body,
        body_html=comment.body_html,
        author_name=comment.author.name if comment.author else "",
        author_flair_text=comment.author_flair_text,
        permalink=comment.permalink,
        created_utc=comment.created_utc,
        is_root=comment.is_root,
        parent_id=comment.parent_id,
        submission_id=comment.submission.id,
        saved=comment.saved,
    )
