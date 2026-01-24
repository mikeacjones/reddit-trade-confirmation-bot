"""Shared configuration and utilities for Temporal bot."""

import os
import re
import logging
import sys
from typing import Optional
from dataclasses import dataclass

import praw
import prawcore.exceptions
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ============================================================================
# Configuration
# ============================================================================

SUBREDDIT_NAME = os.environ["SUBREDDIT_NAME"]
MONTHLY_POST_FLAIR_ID = os.getenv("MONTHLY_POST_FLAIR_ID", None)
TASK_QUEUE = "trade-confirmation-bot"

FLAIR_PATTERN = re.compile(r"Trades: (\d+)")
FLAIR_TEMPLATE_PATTERN = re.compile(r"Trades: ((\d+)-(\d+))")

# ============================================================================
# Logging Setup
# ============================================================================


def setup_logger(name: str) -> logging.Logger:
    """Set up logger with file and console handlers."""
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # File handler
    file_handler = logging.FileHandler("log.txt", mode="a")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


LOGGER = setup_logger("trade-confirmation-bot")

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
# Reddit Client (created per-activity to avoid serialization issues)
# ============================================================================


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


@dataclass
class FlairUpdateResult:
    """Result of updating a user's flair."""

    username: str
    old_flair: Optional[str]
    new_flair: Optional[str]
    success: bool


# ============================================================================
# Utility Functions
# ============================================================================


def should_process_redditor(redditor) -> bool:
    """Check if redditor should be processed."""
    try:
        if redditor is None:
            return False
        if not hasattr(redditor, "id"):
            return False
        reddit = get_reddit_client()
        bot_user = get_bot_user(reddit)
        if redditor.id == bot_user.id:
            return False
        if hasattr(redditor, "is_suspended") and redditor.is_suspended:
            return False
        return True
    except prawcore.exceptions.NotFound:
        return False


def is_confirming_trade(comment_body: str) -> bool:
    """Check if comment is confirming a trade."""
    return "confirmed" in comment_body.lower()


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
