"""Monthly post workflow for trade confirmation bot."""

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from .comment_processing import CommentPollingWorkflow

from bot.config import SUBREDDIT_NAME
from bot.models import CreateMonthlyPostInput, SubmissionInput

from ..activities import notifications as notification_activities
from ..activities import submissions as submission_activities
from ..shared import PUSHOVER_RETRY_POLICY
from ..shared import REDDIT_RETRY_POLICY_CONSERVATIVE as REDDIT_RETRY_POLICY


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
    async def run(self) -> dict[str, str | None]:
        """Create the monthly confirmation thread and lock the old one after 5 days.

        Returns:
            Result with status and submission_id.
        """
        workflow.logger.info("Starting monthly post workflow for r/%s", SUBREDDIT_NAME)

        await workflow.execute_activity(
            notification_activities.send_pushover_notification,
            args=[f"Creating monthly post for r/{SUBREDDIT_NAME}"],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=PUSHOVER_RETRY_POLICY,
        )

        # Discover the current submission ID (the one we're replacing).
        active_submissions = await workflow.execute_activity(
            submission_activities.fetch_active_submission_ids,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=REDDIT_RETRY_POLICY,
        )
        old_submission_id = active_submissions.current_submission_id

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
            await polling_handle.signal(CommentPollingWorkflow.set_current_submission, new_submission_id)
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
            retry_policy=PUSHOVER_RETRY_POLICY,
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
                retry_policy=PUSHOVER_RETRY_POLICY,
            )

            workflow.logger.info("Locked previous submission: %s", old_submission_id)

        return {
            "status": "created",
            "submission_id": new_submission_id,
            "locked_submission_id": old_submission_id,
        }
