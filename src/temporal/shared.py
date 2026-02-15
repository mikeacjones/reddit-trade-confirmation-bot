"""Shared configuration for Temporal bot.

This module contains only workflow-safe imports (no praw/network libraries).
For Reddit-related utilities, see activities/reddit.py.
"""

import os
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

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
    maximum_interval=timedelta(seconds=30),
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
# Secrets
# ============================================================================

SECRETS = {
    "REDDIT_CLIENT_ID": os.getenv("REDDIT_CLIENT_ID"),
    "REDDIT_CLIENT_SECRET": os.getenv("REDDIT_CLIENT_SECRET"),
    "REDDIT_USER_AGENT": os.getenv("REDDIT_USER_AGENT"),
    "REDDIT_USERNAME": os.getenv("REDDIT_USERNAME"),
    "REDDIT_PASSWORD": os.getenv("REDDIT_PASSWORD"),
    "PUSHOVER_APP_TOKEN": os.getenv("PUSHOVER_APP_TOKEN", ""),
    "PUSHOVER_USER_TOKEN": os.getenv("PUSHOVER_USER_TOKEN", ""),
}

# ============================================================================
# Data Classes for Workflow Communication
# ============================================================================


@dataclass
class CommentData:
    """Serializable comment data for passing between activities."""

    id: str
    body: str
    body_html: str
    author_name: str
    author_flair_text: Optional[str]
    permalink: str
    created_utc: float
    is_root: bool
    parent_id: str
    submission_id: str
    saved: bool


@dataclass
class ValidationResult:
    """Result of validating a confirmation comment."""

    valid: bool
    reason: Optional[str] = None  # Template name to use for error reply
    parent_author: Optional[str] = None
    confirmer: Optional[str] = None
    parent_comment_id: Optional[str] = None
    is_mod_approval: bool = False
    reply_to_comment_id: Optional[str] = None  # Comment to reply to (for mod approvals)


@dataclass
class FlairUpdateResult:
    """Result of updating a user's flair."""

    username: str
    old_flair: Optional[str]
    new_flair: Optional[str]
    success: bool


@dataclass
class FlairIncrementRequest:
    """Request to apply a flair increment for a user."""

    username: str
    request_id: str
    delta: int = 1


@dataclass
class FlairIncrementResult:
    """Result of a coordinated flair increment operation."""

    username: str
    applied: bool
    old_count: Optional[int]
    new_count: Optional[int]
    old_flair: Optional[str]
    new_flair: Optional[str]


# ============================================================================
# Utility Functions (workflow-safe - no praw)
# ============================================================================


def is_confirming_trade(comment_body: str) -> bool:
    """Check if comment is confirming a trade."""
    return "confirmed" in comment_body.lower()
