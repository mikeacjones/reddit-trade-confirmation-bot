"""Temporal-specific shared configuration."""

from datetime import timedelta

from temporalio.common import RetryPolicy

from bot.config import BUILD_ID, DEPLOYMENT_NAME, MONTHLY_POST_FLAIR_ID, SUBREDDIT_NAME, TASK_QUEUE
from bot.models import (
    ActiveSubmissions,
    CommentData,
    CreateMonthlyPostInput,
    FetchCommentsInput,
    FetchCommentsResult,
    FlairIncrementRequest,
    FlairIncrementResult,
    FlairUpdateResult,
    ReplyToCommentInput,
    SetUserFlairInput,
    SubmissionInput,
    UserFlairResult,
    ValidationResult,
)
from bot.rules import FLAIR_PATTERN, FLAIR_TEMPLATE_PATTERN, is_confirming_trade

# ============================================================================
# Configuration
# ============================================================================
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
