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

from bot.config import (
    BUILD_ID,
    DEPLOYMENT_NAME,
    SUBREDDIT_NAME,
    TASK_QUEUE,
    TEMPORAL_HOST,
    TEMPORAL_NAMESPACE,
)
from temporal.activities import (
    create_monthly_post,
    fetch_active_submission_ids,
    get_user_flair,
    lock_submission,
    mark_comment_saved,
    poll_new_comments,
    reply_to_comment,
    send_pushover_notification,
    set_user_flair,
    sticky_submission,
    unsticky_submission,
    validate_confirmation,
)
from temporal.activities.flair import FlairCoordinatorActivity
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


async def _signal_wake_up(client: Client) -> None:
    """Signal running CommentPollingWorkflow to re-evaluate version changes."""
    workflow_id = f"poll-{SUBREDDIT_NAME}"
    try:
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(CommentPollingWorkflow.wake_up)
        logger.info("Sent wake_up signal to %s", workflow_id)
    except Exception:
        logger.info(
            "No running workflow %s to signal (may be first deploy)", workflow_id
        )


async def main():
    """Start the Temporal worker."""
    runtime = _build_runtime()

    logger.info(f"Connecting to Temporal at {TEMPORAL_HOST}")
    client = await Client.connect(
        TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE, runtime=runtime
    )

    logger.info(f"Starting worker for task queue: {TASK_QUEUE}")
    logger.info(f"Monitoring subreddit: r/{SUBREDDIT_NAME}")

    activity_executor = ThreadPoolExecutor(max_workers=32)
    flair_coordinator_activity = FlairCoordinatorActivity(client)

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
            flair_coordinator_activity.request_flair_increment,
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
                "praw", "requests", "urllib3", "bot"
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
        async with worker:
            # Worker is now polling — signal running workflows to wake up
            # so they re-evaluate is_target_worker_deployment_version_changed().
            # This is pretty unnecessary but I'm experimenting with how we make sure we quickly kick off
            # managed upgrades with the worker controller, with workflows that have long running
            # polling activities.
            await asyncio.sleep(30)
            await _signal_wake_up(client)
            # Block until shutdown
            await asyncio.Future()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Worker shutdown requested")


if __name__ == "__main__":
    asyncio.run(main())
