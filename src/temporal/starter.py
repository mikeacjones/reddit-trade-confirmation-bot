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

    # Delete stale lock-submissions schedule (one-time cleanup)
    python -m temporal.starter delete-lock-schedule

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
    ScheduleAlreadyRunningError,
    ScheduleCalendarSpec,
    ScheduleRange,
    ScheduleSpec,
    ScheduleUpdate,
)
from temporalio.exceptions import WorkflowAlreadyStartedError

from bot.config import SUBREDDIT_NAME, TASK_QUEUE, TEMPORAL_HOST, TEMPORAL_NAMESPACE
from temporal.search_attributes import (
    ensure_search_attributes,
    subreddit_search_attributes,
)
from temporal.workflows import (
    CommentPollingWorkflow,
    MonthlyPostWorkflow,
)

# Add src to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def get_client() -> Client:
    """Get Temporal client."""
    return await Client.connect(TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE)


def _monthly_post_schedule() -> Schedule:
    """Build the monthly post schedule with current workflow metadata."""
    return Schedule(
        action=ScheduleActionStartWorkflow(
            MonthlyPostWorkflow.run,
            id=f"monthly-post-{SUBREDDIT_NAME}",
            task_queue=TASK_QUEUE,
            typed_search_attributes=subreddit_search_attributes(SUBREDDIT_NAME),
            static_summary=f"r/{SUBREDDIT_NAME}",
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
    )


async def setup_schedules():
    """Set up the scheduled workflows."""
    client = await get_client()

    await ensure_search_attributes(client, TEMPORAL_NAMESPACE)

    logger.info("Setting up schedules...")

    # Monthly post schedule - 1st of each month at 00:00 UTC
    schedule_id = f"monthly-post-schedule-{SUBREDDIT_NAME}"
    schedule = _monthly_post_schedule()
    try:
        await client.create_schedule(schedule_id, schedule)
        logger.info(
            f"Created schedule: monthly-post-schedule-{SUBREDDIT_NAME} (1st of month at 00:00 UTC)"
        )
    except ScheduleAlreadyRunningError:
        handle = client.get_schedule_handle(schedule_id)

        def update_existing_schedule(input):
            current = input.description.schedule
            return ScheduleUpdate(
                Schedule(
                    action=schedule.action,
                    spec=schedule.spec,
                    policy=current.policy,
                    state=current.state,
                )
            )

        await handle.update(update_existing_schedule)
        logger.info(f"Updated schedule: monthly-post-schedule-{SUBREDDIT_NAME}")

    logger.info("Schedules setup complete")


async def start_polling():
    """Start the comment polling workflow for the subreddit."""
    client = await get_client()
    await ensure_search_attributes(client, TEMPORAL_NAMESPACE)

    workflow_id = f"poll-{SUBREDDIT_NAME}"

    logger.info(f"Starting comment polling for r/{SUBREDDIT_NAME}")

    try:
        await client.start_workflow(
            CommentPollingWorkflow.run,
            id=workflow_id,
            task_queue=TASK_QUEUE,
            search_attributes=subreddit_search_attributes(SUBREDDIT_NAME),
            static_summary=f"r/{SUBREDDIT_NAME}",
        )
        logger.info(f"Started polling workflow: {workflow_id}")
        logger.info(
            f"View in Temporal UI: http://localhost:8233/namespaces/{TEMPORAL_NAMESPACE}/workflows/{workflow_id}"
        )
    except WorkflowAlreadyStartedError:
        logger.info(f"Polling workflow {workflow_id} is already running")


async def trigger_monthly_post():
    """Manually trigger the monthly post workflow."""
    client = await get_client()
    await ensure_search_attributes(client, TEMPORAL_NAMESPACE)

    logger.info("Triggering monthly post workflow...")

    handle = await client.start_workflow(
        MonthlyPostWorkflow.run,
        id=f"monthly-post-manual-{SUBREDDIT_NAME}",
        task_queue=TASK_QUEUE,
        search_attributes=subreddit_search_attributes(SUBREDDIT_NAME),
        static_summary=f"r/{SUBREDDIT_NAME}",
    )

    result = await handle.result()
    logger.info(f"Monthly post result: {result}")
    return result


async def delete_lock_schedule():
    """Delete the stale lock-submissions schedule (one-time cleanup)."""
    client = await get_client()

    schedule_id = f"lock-submissions-schedule-{SUBREDDIT_NAME}"
    try:
        handle = client.get_schedule_handle(schedule_id)
        await handle.delete()
        logger.info(f"Deleted schedule: {schedule_id}")
    except Exception as e:
        logger.info(f"Schedule {schedule_id} not found or already deleted: {e}")


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

        # Query for submission IDs
        subs = await handle.query(CommentPollingWorkflow.get_submission_ids)
        logger.info(f"  Current submission: {subs['current_submission_id']}")
        logger.info(f"  Previous submission: {subs['previous_submission_id']}")
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
    setup               Register search attributes and set up schedules
    start-polling       Start polling for comments on current submission
    create-monthly      Manually trigger monthly post creation
    delete-lock-schedule  Delete stale lock-submissions schedule (one-time cleanup)
    status              Show status of running workflows

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
    elif command == "delete-lock-schedule":
        await delete_lock_schedule()
    elif command == "status":
        await show_status()
    else:
        print(f"Unknown command: {command}")
        print_usage()


if __name__ == "__main__":
    asyncio.run(main())
