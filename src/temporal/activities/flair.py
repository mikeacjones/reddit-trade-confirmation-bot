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
    def get_flair_count(cls, username: str, subreddit) -> int:
        """Get current trade count from user's flair. Returns 0 if no flair."""
        flair_text = next(subreddit.flair(username))["flair_text"]
        if not flair_text:
            return 0
        match = FLAIR_PATTERN.search(flair_text)
        return int(match.group(1)) if match else 0

    @classmethod
    def get_flair_text(cls, username: str, subreddit) -> Optional[str]:
        """Get current flair text for a user."""
        return next(subreddit.flair(username))["flair_text"]

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
async def increment_user_flair(username: str) -> dict:
    """Atomically increment a user's trade count flair by 1.

    This activity reads the current flair and increments it in a single operation.
    If the user has no flair, they start at 1. If they have a custom flair that
    doesn't match the expected pattern, their flair is unchanged.

    The workflow uses comment ID as workflow ID, preventing duplicate processing
    of the same confirmation.

    Returns FlairUpdateResult as dict with username, old_flair, new_flair, and success.
    """
    reddit = get_reddit_client()
    subreddit = get_subreddit(reddit)

    old_flair = FlairManager.get_flair_text(username, subreddit)
    current_count = FlairManager.get_flair_count(username, subreddit)
    new_count = current_count + 1

    new_flair = FlairManager.set_flair(username, new_count, subreddit)
    activity.logger.info(
        "u/%s flair updated: '%s' -> '%s'", username, old_flair, new_flair
    )

    return asdict(
        FlairUpdateResult(
            username=username,
            old_flair=old_flair,
            new_flair=new_flair,
            success=new_flair is not None,
        )
    )
