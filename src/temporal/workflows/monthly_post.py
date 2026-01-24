"""Monthly post workflow for trade confirmation bot."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from ..activities import (
        check_monthly_post_exists,
        unsticky_previous_post,
        create_monthly_post,
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

        Returns:
            Result with status and submission_id if created.
        """
        workflow.logger.info(f"Starting monthly post workflow for r/{SUBREDDIT_NAME}")

        # Check if already created (idempotency check)
        exists = await workflow.execute_activity(
            check_monthly_post_exists,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        if exists:
            workflow.logger.info("Monthly post already exists, skipping creation")
            return {"status": "already_exists"}

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

        # Create the new monthly post
        submission_id = await workflow.execute_activity(
            create_monthly_post,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        if not submission_id:
            # Notify about failure
            await workflow.execute_activity(
                send_pushover_notification,
                args=[f"Failed to create monthly post for r/{SUBREDDIT_NAME}"],
                start_to_close_timeout=timedelta(seconds=30),
            )
            return {"status": "failed"}

        # Notify about success
        await workflow.execute_activity(
            send_pushover_notification,
            args=[f"Created monthly post for r/{SUBREDDIT_NAME}: {submission_id}"],
            start_to_close_timeout=timedelta(seconds=30),
        )

        workflow.logger.info(f"Created monthly post: {submission_id}")

        return {
            "status": "created",
            "submission_id": submission_id,
        }
