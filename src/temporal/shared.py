"""Shared configuration for Temporal bot.

This module contains only workflow-safe imports (no praw/network libraries).
For Reddit-related utilities, see activities/reddit.py.
"""

import os
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import NotRequired, TypedDict

from dotenv import load_dotenv
from temporalio.common import RetryPolicy

# Load environment variables
load_dotenv()

# ============================================================================
# Configuration
# ============================================================================

SUBREDDIT_NAME = os.environ["SUBREDDIT_NAME"]
MONTHLY_POST_FLAIR_ID = os.getenv("MONTHLY_POST_FLAIR_ID", None)
TASK_QUEUE = f"trade-confirmation-bot-{SUBREDDIT_NAME}"

FLAIR_PATTERN = re.compile(r"Trades: (\d+)")
FLAIR_TEMPLATE_PATTERN = re.compile(r"Trades: ((\d+)-(\d+))")

# ============================================================================
# Retry Policies
# ============================================================================

# Error types that indicate bugs/config issues - retrying won't help
NON_RETRYABLE_ERRORS = [
    # Programming/logic errors
    "TypeError",
    "ValueError",
    "KeyError",
    "AttributeError",
    "IndexError",
    "AssertionError",
    # Reddit API errors that indicate bugs, not transient issues
    "prawcore.exceptions.Forbidden",  # Permission denied - config issue
    "prawcore.exceptions.NotFound",  # Resource doesn't exist
    "prawcore.exceptions.BadRequest",  # Malformed request - bug
]

# Standard retry policy for Reddit API calls
# - Retries transient failures (network errors, rate limits, 5xx errors)
# - Does NOT retry programming errors to prevent infinite loops
REDDIT_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=600),
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE_ERRORS,
)

# More conservative retry policy for less critical operations
REDDIT_RETRY_POLICY_CONSERVATIVE = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
    backoff_coefficient=2.0,
    non_retryable_error_types=NON_RETRYABLE_ERRORS,
)

# ============================================================================
# Data Classes for Workflow Communication
# ============================================================================


class CommentData(TypedDict):
    """Serializable comment data for passing between activities."""

    id: str
    body: str
    body_html: str
    author_name: str
    author_flair_text: str | None
    permalink: str
    created_utc: float
    is_root: bool
    parent_id: str
    submission_id: str
    saved: bool


class ValidationResult(TypedDict):
    """Result of validating a confirmation comment."""

    valid: bool
    reason: NotRequired[str | None]
    parent_author: NotRequired[str | None]
    confirmer: NotRequired[str | None]
    parent_comment_id: NotRequired[str | None]
    is_mod_approval: NotRequired[bool]
    reply_to_comment_id: NotRequired[str | None]


class FlairUpdateResult(TypedDict):
    """Result of updating a user's flair."""

    username: str
    old_flair: str | None
    new_flair: str | None
    success: bool


@dataclass
class FlairIncrementRequest:
    """Request to apply a flair increment for a user."""

    username: str
    request_id: str
    delta: int = 1


class FlairIncrementResult(TypedDict):
    """Result of a coordinated flair increment operation."""

    username: str
    applied: bool
    old_count: int | None
    new_count: int | None
    old_flair: str | None
    new_flair: str | None


# ============================================================================
# Utility Functions (workflow-safe - no praw)
# ============================================================================


def is_confirming_trade(comment_body: str) -> bool:
    """Check if comment is confirming a trade."""
    return "confirmed" in comment_body.lower()
