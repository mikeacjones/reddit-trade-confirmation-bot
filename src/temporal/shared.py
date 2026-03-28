"""Shared configuration for Temporal bot.

This module contains only workflow-safe imports (no praw/network libraries).
For Reddit-related utilities, see activities/reddit.py.
"""

import os
import re
from dataclasses import dataclass, field
from datetime import timedelta

from dotenv import load_dotenv
from temporalio.common import RetryPolicy

# Load environment variables
load_dotenv()

# ============================================================================
# Configuration
# ============================================================================
BUILD_ID = os.environ.get("TEMPORAL_WORKER_BUILD_ID", os.environ.get("BUILD_ID", "dev"))
DEPLOYMENT_NAME = os.environ.get("TEMPORAL_DEPLOYMENT_NAME", "reddit-trade-confirmation-bot")

SUBREDDIT_NAME = os.environ["SUBREDDIT_NAME"]
MONTHLY_POST_FLAIR_ID = os.getenv("MONTHLY_POST_FLAIR_ID", None)
TASK_QUEUE = f"trade-confirmation-bot-{SUBREDDIT_NAME}"

FLAIR_PATTERN = re.compile(r"Trades: (\d+)")
FLAIR_TEMPLATE_PATTERN = re.compile(r"Trades: ((\d+)-(\d+))")

# Number of recent comment IDs to track as a scan-termination watermark.
# The workflow maintains this many IDs and passes them to the polling activity.
# comment.saved provides the durable safety net for outage recovery.
WATERMARK_IDS_MAX = 50

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


@dataclass
class CommentData:
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
    submission_stickied: bool | None = None


@dataclass
class ValidationResult:
    """Result of validating a confirmation comment."""

    valid: bool
    reason: str | None = None
    parent_author: str | None = None
    confirmer: str | None = None
    parent_comment_id: str | None = None
    is_mod_approval: bool = False
    reply_to_comment_id: str | None = None


@dataclass
class FlairUpdateResult:
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


@dataclass
class FlairIncrementResult:
    """Result of a coordinated flair increment operation."""

    username: str
    applied: bool
    old_count: int | None = None
    new_count: int | None = None
    old_flair: str | None = None
    new_flair: str | None = None


@dataclass
class FetchCommentsInput:
    """Input for fetching new comments."""

    seen_ids: list[str] = field(default_factory=list)
    refresh_submissions: bool = False


@dataclass
class FetchCommentsResult:
    """Result of fetching new comments."""

    comments: list[CommentData] = field(default_factory=list)
    scanned_ids: list[str] = field(default_factory=list)
    found_seen: bool = True
    listing_exhausted: bool = False
    scanned_count: int = 0


@dataclass
class ReplyToCommentInput:
    """Input for replying to a comment with a template."""

    comment_id: str
    template_name: str
    format_args: dict | None = None


@dataclass
class SetUserFlairInput:
    """Input for setting a user's flair."""

    username: str
    new_count: int
    old_flair: str | None = None


@dataclass
class UserFlairResult:
    """Result of getting a user's current flair."""

    username: str
    flair_text: str | None
    trade_count: int | None
    is_trade_tracked: bool


@dataclass
class StartConfirmationInput:
    """Input for starting a confirmation workflow."""

    workflow_id: str
    comment_data: CommentData


# ============================================================================
# Utility Functions (workflow-safe - no praw)
# ============================================================================


def is_confirming_trade(comment_body: str) -> bool:
    """Check if comment is confirming a trade."""
    return "confirmed" in comment_body.lower()
