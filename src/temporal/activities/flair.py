"""Flair management activities for Temporal bot."""

from dataclasses import asdict
from typing import Optional

from temporalio import activity

from ..shared import (
    FLAIR_PATTERN,
    FLAIR_TEMPLATE_PATTERN,
    FlairUpdateResult,
)
from .reddit import get_reddit_client, get_subreddit


class FlairManager:
    """Manages user flair operations."""

    _templates: Optional[dict] = None
    _moderators: Optional[list] = None

    @classmethod
    def _load_flair_templates(cls, subreddit) -> dict:
        """Load flair templates from subreddit."""
        if cls._templates is not None:
            return cls._templates

        templates = {}
        for template in subreddit.flair.templates:
            match = FLAIR_TEMPLATE_PATTERN.search(template["text"])
            if match:
                min_trades = int(match.group(2))
                max_trades = int(match.group(3))
                templates[(min_trades, max_trades)] = {
                    "id": template["id"],
                    "template": template["text"],
                    "mod_only": template["mod_only"],
                }
                activity.logger.info(
                    "Loaded flair template: %d-%d trades", min_trades, max_trades
                )

        cls._templates = templates
        return templates

    @classmethod
    def _load_moderators(cls, subreddit) -> list:
        """Load list of current moderators."""
        if cls._moderators is not None:
            return cls._moderators

        cls._moderators = [str(mod) for mod in subreddit.moderator()]
        return cls._moderators

    @classmethod
    def _get_flair_template(
        cls, trade_count: int, username: str, subreddit
    ) -> Optional[dict]:
        """Get appropriate flair template for trade count."""
        templates = cls._load_flair_templates(subreddit)
        moderators = cls._load_moderators(subreddit)

        for (min_trades, max_trades), template in templates.items():
            if min_trades <= trade_count <= max_trades:
                if template["mod_only"] == (username in moderators):
                    return template
        return None

    @classmethod
    def _format_flair(cls, flair_template: str, count: int) -> str:
        """Format flair text with trade count."""
        match = FLAIR_TEMPLATE_PATTERN.search(flair_template)
        if not match:
            return flair_template
        start, end = match.span(1)
        return flair_template[:start] + str(count) + flair_template[end:]

    @classmethod
    def set_flair(cls, username: str, count: int, subreddit) -> Optional[str]:
        """Set user's flair to specific trade count."""
        template = cls._get_flair_template(count, username, subreddit)
        if not template:
            activity.logger.warning("No flair template found for %d trades", count)
            return None

        new_flair_text = cls._format_flair(template["template"], count)
        subreddit.flair.set(
            username, text=new_flair_text, flair_template_id=template["id"]
        )
        return new_flair_text

    @classmethod
    def is_moderator(cls, username: str, subreddit) -> bool:
        """Check if user is a moderator."""
        moderators = cls._load_moderators(subreddit)
        return username in moderators


@activity.defn
def get_user_flair(username: str) -> dict:
    """Get a user's current flair information.

    This is a read-only activity that returns the user's current flair text
    and trade count. Used by workflows to calculate new flair values before
    calling set_user_flair.

    Returns dict with username, flair_text, trade_count, and is_trade_tracked.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)

    flair_text = next(subreddit.flair(username))["flair_text"]
    trade_count: Optional[int] = 0
    if flair_text:
        match = FLAIR_PATTERN.search(flair_text)
        trade_count = int(match.group(1)) if match else None

    return {
        "username": username,
        "flair_text": flair_text,
        "trade_count": trade_count,
        "is_trade_tracked": trade_count is not None,
    }


@activity.defn
def set_user_flair(
    username: str,
    new_count: int,
    old_flair: Optional[str] = None,
) -> dict:
    """Set a user's flair to a specific trade count.

    This activity is idempotent - calling it multiple times with the same
    new_count will always result in the same flair being set.

    The workflow is responsible for:
    1. Reading current flair via get_user_flair
    2. Calculating the new count (current + 1)
    3. Passing the exact new_count to this activity

    With a single worker, this ensures that even if the activity retries
    after a crash, the same value is always set.

    Returns FlairUpdateResult as dict with username, old_flair, new_flair, and success.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)

    # Set flair to the exact value specified by the workflow
    new_flair = FlairManager.set_flair(username, new_count, subreddit)
    activity.logger.info("u/%s flair set: '%s' -> '%s'", username, old_flair, new_flair)

    return asdict(
        FlairUpdateResult(
            username=username,
            old_flair=old_flair,
            new_flair=new_flair,
            success=new_flair is not None,
        )
    )
