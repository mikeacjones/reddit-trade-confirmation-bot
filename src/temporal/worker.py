"""Temporal worker for Reddit trade confirmation bot.

This is the main entry point for running the bot.
Start this worker, then use starter.py to set up schedules.

Usage:
    python -m temporal.worker

Environment variables:
    TEMPORAL_HOST: Temporal server address (default: localhost:7233)
    SUBREDDIT_NAME: Reddit subreddit to monitor (required)
    REDDIT_*: Reddit API credentials (required)
"""

import asyncio
import os
import sys

from temporalio.client import Client
from temporalio.worker import Worker

# Add src to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from temporal.workflows import (
    CommentPollingWorkflow,
    ProcessConfirmationWorkflow,
    MonthlyPostWorkflow,
    LockSubmissionsWorkflow,
)
from temporal.activities import (
    fetch_new_comments,
    validate_confirmation,
    increment_user_flair,
    mark_comment_saved,
    reply_to_comment,
    post_confirmation_reply,
    create_monthly_post,
    unsticky_previous_post,
    lock_previous_submissions,
    send_pushover_notification,
)
from temporal.shared import LOGGER, TASK_QUEUE, SUBREDDIT_NAME


async def main():
    """Start the Temporal worker."""
    temporal_host = os.getenv("TEMPORAL_HOST", "localhost:7233")

    LOGGER.info(f"Connecting to Temporal at {temporal_host}")
    client = await Client.connect(temporal_host)

    LOGGER.info(f"Starting worker for task queue: {TASK_QUEUE}")
    LOGGER.info(f"Monitoring subreddit: r/{SUBREDDIT_NAME}")

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[
            CommentPollingWorkflow,
            ProcessConfirmationWorkflow,
            MonthlyPostWorkflow,
            LockSubmissionsWorkflow,
        ],
        activities=[
            fetch_new_comments,
            validate_confirmation,
            increment_user_flair,
            mark_comment_saved,
            reply_to_comment,
            post_confirmation_reply,
            create_monthly_post,
            unsticky_previous_post,
            lock_previous_submissions,
            send_pushover_notification,
        ],
    )

    LOGGER.info("Worker started. Press Ctrl+C to stop.")

    try:
        await worker.run()
    except KeyboardInterrupt:
        LOGGER.info("Worker shutdown requested")


if __name__ == "__main__":
    asyncio.run(main())
