"""Monthly post workflow for trade confirmation bot."""

from datetime import timedelta

from temporalio import workflow

from ..activities import notifications as notification_activities
from ..activities import submissions as submission_activities
from ..shared import REDDIT_RETRY_POLICY_CONSERVATIVE as REDDIT_RETRY_POLICY
from ..shared import SUBREDDIT_NAME


@workflow.defn
class MonthlyPostWorkflow:
    """Creates monthly confirmation thread.

    This workflow is scheduled to run on the 1st of each month.
    It handles:
    - Checking if a post already exists (idempotency)
    - Unstickying the previous month's post
    - Creating the new monthly post
    - Sending notifications
    """

    @workflow.run
    async def run(self) -> dict:
        """Create the monthly confirmation thread.

        The create_monthly_post activity handles idempotency internally -
        if a post already exists for this month, it returns that ID.

        Returns:
            Result with status and submission_id.
        """
        workflow.logger.info(f"Starting monthly post workflow for r/{SUBREDDIT_NAME}")

        # Send notification that we're creating the post
        await workflow.execute_activity(
            notification_activities.send_pushover_notification,
            args=[f"Creating monthly post for r/{SUBREDDIT_NAME}"],
            start_to_close_timeout=timedelta(seconds=30),
        )

        # Unsticky previous post
        previous_submission_data = await workflow.execute_activity(
            submission_activities.unsticky_previous_post,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        # Create the new monthly post (idempotent - returns existing if already created)
        submission_id = await workflow.execute_activity(
            submission_activities.create_monthly_post,
            args=[previous_submission_data],
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        # Signal the polling workflow to refresh its submissions cache
        try:
            polling_handle = workflow.get_external_workflow_handle(
                f"poll-{SUBREDDIT_NAME}"
            )
            await polling_handle.signal("invalidate_submissions")
            workflow.logger.info("Signalled polling workflow to refresh submissions")
        except Exception as exc:
            workflow.logger.warning(
                "Could not signal polling workflow: %s: %s",
                type(exc).__name__,
                exc,
            )

        # Notify about success
        await workflow.execute_activity(
            notification_activities.send_pushover_notification,
            args=[f"Monthly post for r/{SUBREDDIT_NAME}: {submission_id}"],
            start_to_close_timeout=timedelta(seconds=30),
        )

        workflow.logger.info(f"Monthly post: {submission_id}")

        return {
            "status": "created",
            "submission_id": submission_id,
        }
