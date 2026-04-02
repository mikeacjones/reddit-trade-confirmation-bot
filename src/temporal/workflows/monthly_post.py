"""Monthly post workflow for trade confirmation bot."""

from datetime import timedelta

from temporalio import workflow

from ..activities import notifications as notification_activities
from ..activities import submissions as submission_activities
from ..activities import temporal_bridge as bridge_activities
from ..shared import (
    CreateMonthlyPostInput,
    REDDIT_RETRY_POLICY_CONSERVATIVE as REDDIT_RETRY_POLICY,
    SUBREDDIT_NAME,
    SubmissionInput,
)


@workflow.defn
class MonthlyPostWorkflow:
    """Creates monthly confirmation thread and locks the previous one after a delay.

    This workflow is scheduled to run on the 1st of each month.
    It handles:
    - Querying the polling workflow for the current submission ID
    - Creating the new monthly post
    - Signalling the polling workflow immediately with the new submission
    - Stickying the new post and unstickying the old one
    - Sleeping for 5 days, then locking the old submission
    """

    @workflow.run
    async def run(self) -> dict:
        """Create the monthly confirmation thread and lock the old one after 5 days.

        Returns:
            Result with status and submission_id.
        """
        workflow.logger.info("Starting monthly post workflow for r/%s", SUBREDDIT_NAME)

        await workflow.execute_activity(
            notification_activities.send_pushover_notification,
            args=[f"Creating monthly post for r/{SUBREDDIT_NAME}"],
            start_to_close_timeout=timedelta(seconds=30),
        )

        # Discover the current submission ID (the one we're replacing).
        subs = await workflow.execute_activity(
            bridge_activities.query_polling_submissions,
            start_to_close_timeout=timedelta(seconds=30),
        )
        old_submission_id = subs.current_submission_id

        # Fallback: if polling workflow wasn't reachable, bootstrap from Reddit.
        if old_submission_id is None:
            fallback = await workflow.execute_activity(
                submission_activities.fetch_active_submission_ids,
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=REDDIT_RETRY_POLICY,
            )
            old_submission_id = fallback.current_submission_id

        # Create the new monthly post (idempotent).
        new_submission_id = await workflow.execute_activity(
            submission_activities.create_monthly_post,
            args=[CreateMonthlyPostInput(previous_submission_id=old_submission_id)],
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        # Signal the polling workflow immediately so it starts scanning the
        # new submission without waiting for sticky/unsticky to finish.
        try:
            polling_handle = workflow.get_external_workflow_handle(
                f"poll-{SUBREDDIT_NAME}"
            )
            await polling_handle.signal("set_current_submission", new_submission_id)
            workflow.logger.info("Signalled polling workflow with new submission")
        except Exception as exc:
            workflow.logger.warning(
                "Could not signal polling workflow: %s: %s",
                type(exc).__name__,
                exc,
            )

        # Sticky the new post, unsticky the old one.
        await workflow.execute_activity(
            submission_activities.sticky_submission,
            args=[SubmissionInput(submission_id=new_submission_id)],
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        if old_submission_id:
            await workflow.execute_activity(
                submission_activities.unsticky_submission,
                args=[SubmissionInput(submission_id=old_submission_id)],
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=REDDIT_RETRY_POLICY,
            )

        await workflow.execute_activity(
            notification_activities.send_pushover_notification,
            args=[f"Monthly post for r/{SUBREDDIT_NAME}: {new_submission_id}"],
            start_to_close_timeout=timedelta(seconds=30),
        )

        workflow.logger.info("Monthly post created: %s", new_submission_id)

        # Wait 5 days then lock the old submission.
        if old_submission_id:
            await workflow.sleep(timedelta(days=5))

            await workflow.execute_activity(
                submission_activities.lock_submission,
                args=[SubmissionInput(submission_id=old_submission_id)],
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            await workflow.execute_activity(
                notification_activities.send_pushover_notification,
                args=[
                    f"Locked previous submission {old_submission_id} for r/{SUBREDDIT_NAME}"
                ],
                start_to_close_timeout=timedelta(seconds=30),
            )

            workflow.logger.info("Locked previous submission: %s", old_submission_id)

        return {
            "status": "created",
            "submission_id": new_submission_id,
            "locked_submission_id": old_submission_id,
        }
