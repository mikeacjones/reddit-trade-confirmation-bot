"""Lock submissions workflow for trade confirmation bot."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from ..activities import (
        lock_previous_submissions,
        send_pushover_notification,
    )
    from ..shared import SUBREDDIT_NAME


REDDIT_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)


@workflow.defn
class LockSubmissionsWorkflow:
    """Locks submissions from previous months.

    This workflow is scheduled to run on the 5th of each month.
    It locks all non-stickied submissions to prevent new comments
    on old confirmation threads.
    """

    @workflow.run
    async def run(self) -> dict:
        """Lock previous month's submissions.

        Returns:
            Result with status and count of locked submissions.
        """
        workflow.logger.info(f"Starting lock submissions workflow for r/{SUBREDDIT_NAME}")

        # Send notification
        await workflow.execute_activity(
            send_pushover_notification,
            args=[f"Locking previous month's posts for r/{SUBREDDIT_NAME}"],
            start_to_close_timeout=timedelta(seconds=30),
        )

        # Lock submissions
        locked_count = await workflow.execute_activity(
            lock_previous_submissions,
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        workflow.logger.info(f"Locked {locked_count} submissions")

        return {
            "status": "completed",
            "locked_count": locked_count,
        }
