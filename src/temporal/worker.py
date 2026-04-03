"""Temporal worker for Reddit trade confirmation bot.

This is the main entry point for running the bot.
Start this worker, then use starter.py to set up schedules.

Usage:
    python -m temporal.worker

Environment variables:
    TEMPORAL_HOST: Temporal server address (default: localhost:7233)
    TEMPORAL_SDK_METRICS_BIND_ADDRESS: Prometheus bind address for SDK metrics
        (example: 0.0.0.0:9000, disabled if unset)
    SUBREDDIT_NAME: Reddit subreddit to monitor (required)
    REDDIT_*: Reddit API credentials (required)
"""

import asyncio
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.common import VersioningBehavior, WorkerDeploymentVersion
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig
from temporalio.worker import Worker, WorkerDeploymentConfig
from temporalio.worker.workflow_sandbox import (
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)

from temporal.activities import (
    create_monthly_post,
    fetch_active_submission_ids,
    get_user_flair,
    lock_submission,
    mark_comment_saved,
    poll_new_comments,
    reply_to_comment,
    request_flair_increment,
    send_pushover_notification,
    set_user_flair,
    sticky_submission,
    unsticky_submission,
    validate_confirmation,
)
from temporal.shared import BUILD_ID, DEPLOYMENT_NAME, SUBREDDIT_NAME, TASK_QUEUE
from temporal.workflows import (
    CommentPollingWorkflow,
    FlairCoordinatorWorkflow,
    MonthlyPostWorkflow,
    ProcessConfirmationWorkflow,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add src to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build_runtime() -> Runtime | None:
    """Create an SDK runtime with optional Prometheus metrics exporter."""
    bind_address = os.getenv("TEMPORAL_SDK_METRICS_BIND_ADDRESS", "").strip()
    if not bind_address:
        return None

    runtime = Runtime(
        telemetry=TelemetryConfig(
            metrics=PrometheusConfig(bind_address=bind_address),
            global_tags={
                "app": "reddit-trade-confirmation-bot",
                "subreddit": SUBREDDIT_NAME,
            },
        )
    )
    Runtime.set_default(runtime, error_if_already_set=False)
    logger.info("Temporal SDK metrics enabled at http://%s/metrics", bind_address)
    return runtime


async def main():
    """Start the Temporal worker."""
    temporal_host = os.getenv("TEMPORAL_ADDRESS", os.getenv("TEMPORAL_HOST", "localhost:7233"))
    runtime = _build_runtime()

    logger.info(f"Connecting to Temporal at {temporal_host}")
    client = await Client.connect(temporal_host, namespace="reddit-bots", runtime=runtime)

    logger.info(f"Starting worker for task queue: {TASK_QUEUE}")
    logger.info(f"Monitoring subreddit: r/{SUBREDDIT_NAME}")

    activity_executor = ThreadPoolExecutor(max_workers=32)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[
            CommentPollingWorkflow,
            ProcessConfirmationWorkflow,
            FlairCoordinatorWorkflow,
            MonthlyPostWorkflow,
        ],
        activities=[
            poll_new_comments,
            validate_confirmation,
            get_user_flair,
            set_user_flair,
            request_flair_increment,
            mark_comment_saved,
            reply_to_comment,
            create_monthly_post,
            fetch_active_submission_ids,
            sticky_submission,
            unsticky_submission,
            lock_submission,
            send_pushover_notification,
        ],
        activity_executor=activity_executor,
        workflow_runner=SandboxedWorkflowRunner(
            restrictions=SandboxRestrictions.default.with_passthrough_modules(
                "praw", "requests", "urllib3"
            )
        ),
        deployment_config=WorkerDeploymentConfig(
            version=WorkerDeploymentVersion(
                deployment_name=DEPLOYMENT_NAME,
                build_id=BUILD_ID,
            ),
            use_worker_versioning=True,
            default_versioning_behavior=VersioningBehavior.PINNED,
        ),
    )

    logger.info("Worker started. Press Ctrl+C to stop.")

    try:
        await worker.run()
    except KeyboardInterrupt:
        logger.info("Worker shutdown requested")


if __name__ == "__main__":
    asyncio.run(main())
