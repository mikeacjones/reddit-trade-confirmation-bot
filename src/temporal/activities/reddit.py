"""Compatibility wrapper for Reddit helpers used by activities."""

from bot.reddit import (
    get_bot_user,
    get_reddit_client,
    get_subreddit,
    serialize_comment,
    should_process_redditor,
)

__all__ = [
    "get_reddit_client",
    "get_subreddit",
    "get_bot_user",
    "should_process_redditor",
    "serialize_comment",
]
