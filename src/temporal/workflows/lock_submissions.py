"""Lock submissions workflow for trade confirmation bot."""

from datetime import timedelta

from temporalio import workflow

from ..activities import notifications as notification_activities
from ..activities import submissions as submission_activities
from ..shared import REDDIT_RETRY_POLICY_CONSERVATIVE as REDDIT_RETRY_POLICY
from ..shared import SUBREDDIT_NAME


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
        workflow.logger.info(
            f"Starting lock submissions workflow for r/{SUBREDDIT_NAME}"
        )

        # Send notification
        try:
            await workflow.execute_activity(
                notification_activities.send_pushover_notification,
                args=[f"Locking previous month's posts for r/{SUBREDDIT_NAME}"],
                start_to_close_timeout=timedelta(seconds=30),
            )
        except Exception as exc:
            workflow.logger.warning(
                "Failed to send lock-submissions start notification: %s: %s",
                type(exc).__name__,
                str(exc),
            )

        # Lock submissions
        locked_count = await workflow.execute_activity(
            submission_activities.lock_previous_submissions,
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        workflow.logger.info(f"Locked {locked_count} submissions")

        return {
            "status": "completed",
            "locked_count": locked_count,
        }
