"""App configuration shared outside the Temporal layer."""

import os
import re

from dotenv import load_dotenv

load_dotenv()

SUBREDDIT_NAME = os.environ["SUBREDDIT_NAME"]


def slugify_subreddit_name(subreddit_name: str) -> str:
    """Convert a subreddit name into a Docker/Temporal-safe slug."""
    slug = re.sub(r"[^a-z0-9.-]+", "-", subreddit_name.lower())
    slug = re.sub(r"-+", "-", slug).strip("-.")
    return slug or "unknown"


SUBREDDIT_SLUG = slugify_subreddit_name(SUBREDDIT_NAME)
BUILD_ID = os.environ.get("TEMPORAL_WORKER_BUILD_ID", os.environ.get("BUILD_ID", "dev"))
DEPLOYMENT_NAME = os.environ.get(
    "TEMPORAL_DEPLOYMENT_NAME",
    f"reddit-bots-trade-confirmation-{SUBREDDIT_SLUG}",
)
TEMPORAL_HOST = os.getenv("TEMPORAL_ADDRESS", os.getenv("TEMPORAL_HOST", "localhost:7233"))
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "reddit-bots")
MONTHLY_POST_FLAIR_ID = os.getenv("MONTHLY_POST_FLAIR_ID", None)
TASK_QUEUE = f"trade-confirmation-bot-{SUBREDDIT_NAME}"
