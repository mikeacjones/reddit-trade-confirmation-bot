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
import logging
import os
import sys

from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import (
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add src to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from temporal.activities import (
    create_monthly_post,
    fetch_new_comments,
    get_user_flair,
    lock_previous_submissions,
    mark_comment_saved,
    post_confirmation_reply,
    request_flair_increment,
    reply_to_comment,
    send_pushover_notification,
    set_user_flair,
    unsticky_previous_post,
    validate_confirmation,
)
from temporal.shared import SUBREDDIT_NAME, TASK_QUEUE
from temporal.workflows import (
    CommentPollingWorkflow,
    FlairCoordinatorWorkflow,
    LockSubmissionsWorkflow,
    MonthlyPostWorkflow,
    ProcessConfirmationWorkflow,
)


async def main():
    """Start the Temporal worker."""
    temporal_host = os.getenv("TEMPORAL_HOST", "localhost:7233")

    logger.info(f"Connecting to Temporal at {temporal_host}")
    client = await Client.connect(temporal_host)

    logger.info(f"Starting worker for task queue: {TASK_QUEUE}")
    logger.info(f"Monitoring subreddit: r/{SUBREDDIT_NAME}")

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[
            CommentPollingWorkflow,
            ProcessConfirmationWorkflow,
            FlairCoordinatorWorkflow,
            MonthlyPostWorkflow,
            LockSubmissionsWorkflow,
        ],
        activities=[
            fetch_new_comments,
            validate_confirmation,
            get_user_flair,
            set_user_flair,
            request_flair_increment,
            mark_comment_saved,
            reply_to_comment,
            post_confirmation_reply,
            create_monthly_post,
            unsticky_previous_post,
            lock_previous_submissions,
            send_pushover_notification,
        ],
        workflow_runner=SandboxedWorkflowRunner(
            restrictions=SandboxRestrictions.default.with_passthrough_modules(
                "praw", "requests", "urllib3"
            )
        ),
    )

    logger.info("Worker started. Press Ctrl+C to stop.")

    try:
        await worker.run()
    except KeyboardInterrupt:
        logger.info("Worker shutdown requested")


if __name__ == "__main__":
    asyncio.run(main())
