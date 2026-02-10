"""Reddit client utilities for activities.

This module contains praw-related code that can only be used in activities,
not in workflows (due to Temporal sandbox restrictions).
"""

import time

import praw
import praw.models

from ..shared import SECRETS, SUBREDDIT_NAME, CommentData

_reddit_client: praw.Reddit | None = None
_bot_user: praw.models.Redditor | None = None
_bot_submissions_cache: dict[str, praw.models.Submission] | None = None
_bot_submissions_cache_loaded_at: float | None = None
_bot_submissions_cache_limit: int | None = None

BOT_SUBMISSIONS_CACHE_TTL_SECONDS = 300


def get_reddit_client() -> praw.Reddit:
    """Get the shared Reddit client instance."""
    global _reddit_client
    if _reddit_client is None:
        _reddit_client = praw.Reddit(
            client_id=SECRETS["REDDIT_CLIENT_ID"],
            client_secret=SECRETS["REDDIT_CLIENT_SECRET"],
            user_agent=SECRETS["REDDIT_USER_AGENT"],
            username=SECRETS["REDDIT_USERNAME"],
            password=SECRETS["REDDIT_PASSWORD"],
        )
    return _reddit_client


def get_subreddit(reddit: praw.Reddit) -> praw.models.Subreddit:
    """Get the configured subreddit."""
    return reddit.subreddit(SUBREDDIT_NAME)


def get_bot_user(reddit: praw.Reddit) -> praw.models.Redditor:
    """Get the bot user."""
    global _bot_user
    if _bot_user is None:
        user = reddit.user.me()
        if user is None:
            raise RuntimeError("Reddit client is in read-only mode - not authenticated")
        _bot_user = user
    return _bot_user


def get_bot_submissions(
    reddit: praw.Reddit,
    limit: int = 10,
    max_age_seconds: int = BOT_SUBMISSIONS_CACHE_TTL_SECONDS,
) -> dict[str, praw.models.Submission]:
    """Get recent bot submissions with a short-lived cache."""
    global _bot_submissions_cache
    global _bot_submissions_cache_loaded_at
    global _bot_submissions_cache_limit

    now = time.monotonic()
    cache_fresh = (
        _bot_submissions_cache is not None
        and _bot_submissions_cache_loaded_at is not None
        and _bot_submissions_cache_limit == limit
        and now - _bot_submissions_cache_loaded_at < max_age_seconds
    )
    if cache_fresh:
        return _bot_submissions_cache

    bot_user = get_bot_user(reddit)
    _bot_submissions_cache = {s.id: s for s in bot_user.submissions.new(limit=limit)}
    _bot_submissions_cache_loaded_at = now
    _bot_submissions_cache_limit = limit
    return _bot_submissions_cache


def invalidate_bot_submissions_cache() -> None:
    """Invalidate cached bot submissions so next read is fresh."""
    global _bot_submissions_cache
    global _bot_submissions_cache_loaded_at
    global _bot_submissions_cache_limit

    _bot_submissions_cache = None
    _bot_submissions_cache_loaded_at = None
    _bot_submissions_cache_limit = None


def normalize_username(username: str | None) -> str:
    """Normalize a username for case-insensitive comparisons."""
    if not username:
        return ""

    value = username.strip()
    lower = value.lower()
    if lower.startswith("/u/"):
        value = value[3:]
    elif lower.startswith("u/"):
        value = value[2:]

    return value.strip().casefold()


def should_process_redditor(redditor, bot_username: str) -> bool:
    """Check if redditor should be processed.

    Args:
        redditor: The redditor object to check.
        bot_username: The bot username (for self-comment filtering).
    """
    if redditor is None:
        return False

    # Avoid lazy Redditor fetches: only use already-present name field.
    author_name = getattr(redditor, "name", None)
    normalized_author = normalize_username(author_name)
    if not normalized_author:
        return False

    normalized_bot = normalize_username(bot_username)
    if normalized_bot and normalized_author == normalized_bot:
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
