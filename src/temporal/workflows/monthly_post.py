"""Monthly post workflow for trade confirmation bot."""

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from ..activities import (
        unsticky_previous_post,
        create_monthly_post,
        send_pushover_notification,
    )
    from ..shared import SUBREDDIT_NAME, REDDIT_RETRY_POLICY_CONSERVATIVE as REDDIT_RETRY_POLICY


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
            send_pushover_notification,
            args=[f"Creating monthly post for r/{SUBREDDIT_NAME}"],
            start_to_close_timeout=timedelta(seconds=30),
        )

        # Unsticky previous post
        await workflow.execute_activity(
            unsticky_previous_post,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        # Create the new monthly post (idempotent - returns existing if already created)
        submission_id = await workflow.execute_activity(
            create_monthly_post,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        # Notify about success
        await workflow.execute_activity(
            send_pushover_notification,
            args=[f"Monthly post for r/{SUBREDDIT_NAME}: {submission_id}"],
            start_to_close_timeout=timedelta(seconds=30),
        )

        workflow.logger.info(f"Monthly post: {submission_id}")

        return {
            "status": "created",
            "submission_id": submission_id,
        }
