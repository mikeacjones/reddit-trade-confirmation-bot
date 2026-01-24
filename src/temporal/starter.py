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
import os
import sys
from datetime import timedelta

from temporalio.client import Client, Schedule, ScheduleSpec, ScheduleCalendarSpec
from temporalio.client import ScheduleActionStartWorkflow
from temporalio.common import RetryPolicy

# Add src to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from temporal.workflows import (
    CommentPollingWorkflow,
    MonthlyPostWorkflow,
    LockSubmissionsWorkflow,
)
from temporal.activities import get_current_submission_id
from temporal.shared import LOGGER, TASK_QUEUE, SUBREDDIT_NAME, get_reddit_client, get_bot_user


async def get_client() -> Client:
    """Get Temporal client."""
    temporal_host = os.getenv("TEMPORAL_HOST", "localhost:7233")
    return await Client.connect(temporal_host)


async def setup_schedules():
    """Set up the scheduled workflows."""
    client = await get_client()

    LOGGER.info("Setting up schedules...")

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
                            day_of_month=[1],
                            hour=[0],
                            minute=[0],
                        )
                    ]
                ),
            ),
        )
        LOGGER.info("Created schedule: monthly-post-schedule (1st of month at 00:00 UTC)")
    except Exception as e:
        if "already exists" in str(e).lower():
            LOGGER.info("Schedule monthly-post-schedule already exists")
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
                            day_of_month=[5],
                            hour=[0],
                            minute=[0],
                        )
                    ]
                ),
            ),
        )
        LOGGER.info("Created schedule: lock-submissions-schedule (5th of month at 00:00 UTC)")
    except Exception as e:
        if "already exists" in str(e).lower():
            LOGGER.info("Schedule lock-submissions-schedule already exists")
        else:
            raise

    LOGGER.info("Schedules setup complete")


async def start_polling():
    """Start the comment polling workflow for the current submission."""
    client = await get_client()

    # Get current submission ID from Reddit
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)

    try:
        current_submission = next(bot_user.submissions.new(limit=1))
        submission_id = current_submission.id
    except StopIteration:
        LOGGER.error("No submissions found. Create a monthly post first.")
        return

    workflow_id = f"poll-{submission_id}"

    LOGGER.info(f"Starting comment polling for submission {submission_id}")

    try:
        handle = await client.start_workflow(
            CommentPollingWorkflow.run,
            args=[submission_id, 30],  # 30 second poll interval
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )
        LOGGER.info(f"Started polling workflow: {workflow_id}")
        LOGGER.info(f"View in Temporal UI: http://localhost:8233/namespaces/default/workflows/{workflow_id}")
    except Exception as e:
        if "already started" in str(e).lower() or "already exists" in str(e).lower():
            LOGGER.info(f"Polling workflow {workflow_id} is already running")
        else:
            raise


async def trigger_monthly_post():
    """Manually trigger the monthly post workflow."""
    client = await get_client()

    LOGGER.info("Triggering monthly post workflow...")

    handle = await client.start_workflow(
        MonthlyPostWorkflow.run,
        id="monthly-post-manual",
        task_queue=TASK_QUEUE,
    )

    result = await handle.result()
    LOGGER.info(f"Monthly post result: {result}")
    return result


async def trigger_lock_submissions():
    """Manually trigger the lock submissions workflow."""
    client = await get_client()

    LOGGER.info("Triggering lock submissions workflow...")

    handle = await client.start_workflow(
        LockSubmissionsWorkflow.run,
        id="lock-submissions-manual",
        task_queue=TASK_QUEUE,
    )

    result = await handle.result()
    LOGGER.info(f"Lock submissions result: {result}")
    return result


async def show_status():
    """Show status of running workflows."""
    client = await get_client()

    LOGGER.info("Checking workflow status...")

    # Get current submission
    reddit = get_reddit_client()
    bot_user = get_bot_user(reddit)

    try:
        current_submission = next(bot_user.submissions.new(limit=1))
        submission_id = current_submission.id
        workflow_id = f"poll-{submission_id}"

        try:
            handle = client.get_workflow_handle(workflow_id)
            desc = await handle.describe()
            LOGGER.info(f"Polling workflow: {desc.status.name}")

            # Query for status
            status = await handle.query(CommentPollingWorkflow.get_status)
            LOGGER.info(f"  Processed comments: {status['processed_count']}")
            LOGGER.info(f"  Last seen ID: {status['last_seen_id']}")
        except Exception as e:
            LOGGER.info(f"Polling workflow not found: {e}")
    except StopIteration:
        LOGGER.info("No submissions found")

    # List schedules
    LOGGER.info("\nSchedules:")
    async for schedule in client.list_schedules():
        LOGGER.info(f"  - {schedule.id}")


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
