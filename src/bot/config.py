"""App configuration shared outside the Temporal layer."""

import os

from dotenv import load_dotenv

load_dotenv()

BUILD_ID = os.environ.get("TEMPORAL_WORKER_BUILD_ID", os.environ.get("BUILD_ID", "dev"))
DEPLOYMENT_NAME = os.environ.get("TEMPORAL_DEPLOYMENT_NAME", "reddit-trade-confirmation-bot")
TEMPORAL_HOST = os.getenv("TEMPORAL_ADDRESS", os.getenv("TEMPORAL_HOST", "localhost:7233"))
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "reddit-bots")
SUBREDDIT_NAME = os.environ["SUBREDDIT_NAME"]
MONTHLY_POST_FLAIR_ID = os.getenv("MONTHLY_POST_FLAIR_ID", None)
TASK_QUEUE = f"trade-confirmation-bot-{SUBREDDIT_NAME}"
