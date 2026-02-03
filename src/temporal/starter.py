"""Temporal workflow starter and schedule setup.

This script sets up the scheduled workflows and starts the initial
comment polling workflow.

Usage:
    # Set up schedules (run once)
    python -m temporal.starter setup

    # Start polling the current submission
    python -m temporal.starter start-polling

    # Manually trigger monthly post
    python -m temporal.starter create-monthly

    # Manually trigger lock submissions
    python -m temporal.starter lock-submissions

Environment variables:
    TEMPORAL_HOST: Temporal server address (default: localhost:7233)
    SUBREDDIT_NAME: Reddit subreddit to monitor (required)
"""

import asyncio
import logging
import os
import sys

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleCalendarSpec,
    ScheduleRange,
    ScheduleSpec,
)

# Add src to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from temporal.shared import SUBREDDIT_NAME, TASK_QUEUE
from temporal.workflows import (
    CommentPollingWorkflow,
    LockSubmissionsWorkflow,
    MonthlyPostWorkflow,
)


async def get_client() -> Client:
    """Get Temporal client."""
    temporal_host = os.getenv("TEMPORAL_HOST", "localhost:7233")
    return await Client.connect(temporal_host)


async def setup_schedules():
    """Set up the scheduled workflows."""
    client = await get_client()

    logger.info("Setting up schedules...")

    # Monthly post schedule - 1st of each month at 00:00 UTC
    try:
        await client.create_schedule(
            "monthly-post-schedule",
            Schedule(
                action=ScheduleActionStartWorkflow(
                    MonthlyPostWorkflow.run,
                    id="monthly-post",
                    task_queue=TASK_QUEUE,
                ),
                spec=ScheduleSpec(
                    calendars=[
                        ScheduleCalendarSpec(
                            day_of_month=[ScheduleRange(start=1)],
                            hour=[ScheduleRange(start=0)],
                            minute=[ScheduleRange(start=0)],
                        )
                    ]
                ),
            ),
        )
        logger.info(
            "Created schedule: monthly-post-schedule (1st of month at 00:00 UTC)"
        )
    except Exception as e:
        if "already exists" in str(e).lower():
            logger.info("Schedule monthly-post-schedule already exists")
        else:
            raise

    # Lock submissions schedule - 5th of each month at 00:00 UTC
    try:
        await client.create_schedule(
            "lock-submissions-schedule",
            Schedule(
                action=ScheduleActionStartWorkflow(
                    LockSubmissionsWorkflow.run,
                    id="lock-submissions",
                    task_queue=TASK_QUEUE,
                ),
                spec=ScheduleSpec(
                    calendars=[
                        ScheduleCalendarSpec(
                            day_of_month=[ScheduleRange(start=5)],
                            hour=[ScheduleRange(start=0)],
                            minute=[ScheduleRange(start=0)],
                        )
                    ]
                ),
            ),
        )
        logger.info(
            "Created schedule: lock-submissions-schedule (5th of month at 00:00 UTC)"
        )
    except Exception as e:
        if "already exists" in str(e).lower():
            logger.info("Schedule lock-submissions-schedule already exists")
        else:
            raise

    logger.info("Schedules setup complete")


async def start_polling():
    """Start the comment polling workflow for the subreddit."""
    client = await get_client()

    workflow_id = f"poll-{SUBREDDIT_NAME}"

    logger.info(f"Starting comment polling for r/{SUBREDDIT_NAME}")

    try:
        await client.start_workflow(
            CommentPollingWorkflow.run,
            args=[30],  # 30 second poll interval
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )
        logger.info(f"Started polling workflow: {workflow_id}")
        logger.info(
            f"View in Temporal UI: http://localhost:8233/namespaces/default/workflows/{workflow_id}"
        )
    except Exception as e:
        if "already started" in str(e).lower() or "already exists" in str(e).lower():
            logger.info(f"Polling workflow {workflow_id} is already running")
        else:
            raise


async def trigger_monthly_post():
    """Manually trigger the monthly post workflow."""
    client = await get_client()

    logger.info("Triggering monthly post workflow...")

    handle = await client.start_workflow(
        MonthlyPostWorkflow.run,
        id="monthly-post-manual",
        task_queue=TASK_QUEUE,
    )

    result = await handle.result()
    logger.info(f"Monthly post result: {result}")
    return result


async def trigger_lock_submissions():
    """Manually trigger the lock submissions workflow."""
    client = await get_client()

    logger.info("Triggering lock submissions workflow...")

    handle = await client.start_workflow(
        LockSubmissionsWorkflow.run,
        id="lock-submissions-manual",
        task_queue=TASK_QUEUE,
    )

    result = await handle.result()
    logger.info(f"Lock submissions result: {result}")
    return result


async def show_status():
    """Show status of running workflows."""
    client = await get_client()

    logger.info("Checking workflow status...")

    workflow_id = f"poll-{SUBREDDIT_NAME}"

    try:
        handle = client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        if desc.status is None:
            raise RuntimeError(f"Workflow {workflow_id} has no status")
        logger.info(f"Polling workflow: {desc.status.name}")

        # Query for status
        status = await handle.query(CommentPollingWorkflow.get_status)
        logger.info(f"  Processed comments: {status['processed_count']}")
        logger.info(f"  Last seen ID: {status['last_seen_id']}")
    except Exception as e:
        logger.info(f"Polling workflow not found: {e}")

    # List schedules
    logger.info("\nSchedules:")
    schedules = await client.list_schedules()
    async for schedule in schedules:
        logger.info(f"  - {schedule.id}")


def print_usage():
    """Print usage information."""
    print("""
Usage: python -m temporal.starter <command>

Commands:
    setup           Set up scheduled workflows (run once)
    start-polling   Start polling for comments on current submission
    create-monthly  Manually trigger monthly post creation
    lock-submissions Manually trigger lock submissions
    status          Show status of running workflows

Make sure the worker is running before executing commands:
    python -m temporal.worker
""")


async def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print_usage()
        return

    command = sys.argv[1]

    if command == "setup":
        await setup_schedules()
    elif command == "start-polling":
        await start_polling()
    elif command == "create-monthly":
        await trigger_monthly_post()
    elif command == "lock-submissions":
        await trigger_lock_submissions()
    elif command == "status":
        await show_status()
    else:
        print(f"Unknown command: {command}")
        print_usage()


if __name__ == "__main__":
    asyncio.run(main())
