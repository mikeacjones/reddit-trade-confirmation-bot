"""Grouped Reddit adapter around PRAW access."""

import os

import praw
import praw.models

from .config import SUBREDDIT_NAME
from .models import CommentData

_reddit_client: praw.Reddit | None = None
_subreddit: praw.models.Subreddit | None = None
_bot_user: praw.models.Redditor | None = None


def get_reddit_client() -> praw.Reddit:
    """Get the shared Reddit client instance."""
    global _reddit_client
    if _reddit_client is None:
        _reddit_client = praw.Reddit(
            client_id=os.environ["REDDIT_CLIENT_ID"],
            client_secret=os.environ["REDDIT_CLIENT_SECRET"],
            user_agent=os.environ["REDDIT_USER_AGENT"],
            username=os.environ["REDDIT_USERNAME"],
            password=os.environ["REDDIT_PASSWORD"],
        )
    return _reddit_client


def get_subreddit(reddit: praw.Reddit | None = None) -> praw.models.Subreddit:
    """Get the configured subreddit (cached)."""
    global _subreddit
    if _subreddit is None:
        if reddit is None:
            reddit = get_reddit_client()
        _subreddit = reddit.subreddit(SUBREDDIT_NAME)
    return _subreddit


def get_bot_user(reddit: praw.Reddit) -> praw.models.Redditor:
    """Get the authenticated bot user."""
    global _bot_user
    if _bot_user is None:
        user = reddit.user.me()
        if user is None:
            raise RuntimeError("Reddit client is in read-only mode - not authenticated")
        _bot_user = user
    return _bot_user


def should_process_redditor(redditor, bot_user) -> bool:
    """Return whether the bot should act on the given redditor."""
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
    """Convert a PRAW comment to a plain bot model."""
    return CommentData(
        id=comment.id,
        body=comment.body,
        author_name=comment.author.name if comment.author else "",
        created_utc=comment.created_utc,
        is_root=comment.is_root,
        submission_id=comment.submission.id,
    )
