"""Comment processing workflows for trade confirmation."""

from datetime import timedelta
from typing import Optional

from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from ..activities import comments as comment_activities
from ..activities import flair as flair_activities
from ..shared import REDDIT_RETRY_POLICY


@workflow.defn
class CommentPollingWorkflow:
    """Continuously polls for new comments and processes them.

    This workflow runs indefinitely, periodically checking for new comments
    in the specified submission and spawning child workflows to process them.
    """

    # Continue-as-new after this many iterations to prevent unbounded event history
    MAX_ITERATIONS = 50

    def __init__(self):
        self._should_stop = False
        self._last_seen_id: Optional[str] = None
        self._processed_count = 0
        self._iterations = 0

    @workflow.signal
    def stop(self) -> None:
        """Signal to stop the polling loop."""
        self._should_stop = True

    @workflow.query
    def get_status(self) -> dict:
        """Query the current status of the polling workflow."""
        return {
            "last_seen_id": self._last_seen_id,
            "processed_count": self._processed_count,
            "running": not self._should_stop,
        }

    @workflow.run
    async def run(
        self,
        poll_interval_seconds: int = 30,
        last_seen_id: Optional[str] = None,
    ) -> dict:
        """Run the comment polling loop.

        Args:
            poll_interval_seconds: How often to poll for new comments.
            last_seen_id: Last processed comment ID (carried over from continue-as-new).

        Returns:
            Final status when stopped.
        """
        self._last_seen_id = last_seen_id
        workflow.logger.info("Starting comment polling for subreddit")

        while not self._should_stop:
            # Fetch new comments - let exceptions propagate after retries exhausted
            poll_result = await workflow.execute_activity(
                comment_activities.fetch_new_comments,
                args=[self._last_seen_id],
                start_to_close_timeout=timedelta(seconds=120),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=REDDIT_RETRY_POLICY,
            )
            comments = poll_result["comments"]

            # Advance watermark even when comments are skipped and not sent to child workflows.
            newest_seen_id = poll_result.get("newest_seen_id")
            if newest_seen_id and (
                self._last_seen_id is None
                or int(newest_seen_id, 36) > int(self._last_seen_id, 36)
            ):
                self._last_seen_id = newest_seen_id

            # Process each comment via child workflow
            for comment_data in comments:
                # Use comment ID as workflow ID for idempotency
                # If we crash and restart, already-processed comments won't be reprocessed
                workflow_id = f"process-{comment_data['id']}"

                try:
                    await workflow.execute_child_workflow(
                        ProcessConfirmationWorkflow.run,
                        args=[comment_data],
                        id=workflow_id,
                        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
                    )
                    self._processed_count += 1
                except WorkflowAlreadyStartedError:
                    workflow.logger.warning(
                        "Comment %s already processed (child workflow %s exists), skipping",
                        comment_data["id"],
                        workflow_id,
                    )

            # Wait before next poll (durable timer - survives worker restarts)
            await workflow.sleep(timedelta(seconds=poll_interval_seconds))

            self._iterations += 1
            if self._iterations >= self.MAX_ITERATIONS:
                workflow.logger.info(
                    "Continuing as new after %d iterations", self._iterations
                )
                workflow.continue_as_new(
                    args=[poll_interval_seconds, self._last_seen_id]
                )

        workflow.logger.info("Comment polling stopped")
        return self.get_status()


@workflow.defn
class ProcessConfirmationWorkflow:
    """Process a single comment for potential trade confirmation.

    This workflow handles:
    - Validating the comment is a valid trade confirmation
    - Updating both users' flairs
    - Posting the confirmation reply
    - Handling error cases with appropriate template responses
    """

    @workflow.run
    async def run(self, comment_data: dict) -> dict:
        """Process a potential trade confirmation comment.

        Args:
            comment_data: Serialized CommentData dict.

        Returns:
            Processing result with status and details.
        """
        comment_id = comment_data["id"]
        author = comment_data["author_name"]

        workflow.logger.info(f"Processing comment {comment_id} by {author}")

        # Validate the confirmation
        validation = await workflow.execute_activity(
            comment_activities.validate_confirmation,
            args=[comment_data],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        # Mark confirming comment as saved so it won't be re-fetched.
        # This is needed because Temporal's default WorkflowIDReusePolicy
        # (ALLOW_DUPLICATE) permits re-starting completed child workflows,
        # so the workflow ID alone isn't sufficient dedup.
        await workflow.execute_activity(
            comment_activities.mark_comment_saved,
            args=[comment_id],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        # Handle validation failure with template response
        if not validation["valid"]:
            reason = validation.get("reason")
            if reason:
                # Reply with error template
                await workflow.execute_activity(
                    comment_activities.reply_to_comment,
                    args=[comment_id, reason, {"comment": comment_data}],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=REDDIT_RETRY_POLICY,
                )
                return {
                    "status": "rejected",
                    "reason": reason,
                    "comment_id": comment_id,
                }

            return {"status": "skipped", "comment_id": comment_id}

        # Valid confirmation - update flairs
        parent_author = validation["parent_author"]
        confirmer = validation["confirmer"]
        parent_comment_id = validation.get("parent_comment_id")

        # Read current flair values first (these get cached by Temporal on replay)
        parent_flair = await workflow.execute_activity(
            flair_activities.get_user_flair,
            args=[parent_author],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        confirmer_flair = await workflow.execute_activity(
            flair_activities.get_user_flair,
            args=[confirmer],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        # Calculate new flair values in the workflow (deterministic)
        parent_new_count = parent_flair["trade_count"] + 1
        confirmer_new_count = confirmer_flair["trade_count"] + 1

        # Set flairs to exact values - idempotent even on retry/replay
        parent_result = await workflow.execute_activity(
            flair_activities.set_user_flair,
            args=[parent_author, parent_new_count],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        confirmer_result = await workflow.execute_activity(
            flair_activities.set_user_flair,
            args=[confirmer, confirmer_new_count],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        # Mark parent comment as saved too
        if parent_comment_id:
            await workflow.execute_activity(
                comment_activities.mark_comment_saved,
                args=[parent_comment_id],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=REDDIT_RETRY_POLICY,
            )

        # Post confirmation reply
        await workflow.execute_activity(
            comment_activities.post_confirmation_reply,
            args=[
                comment_id,
                parent_author,
                confirmer,
                parent_result.get("old_flair"),
                parent_result.get("new_flair"),
                confirmer_result.get("old_flair"),
                confirmer_result.get("new_flair"),
            ],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        workflow.logger.info(
            f"Confirmed trade: {parent_author} ({parent_result.get('new_flair')}) "
            f"<-> {confirmer} ({confirmer_result.get('new_flair')})"
        )

        return {
            "status": "confirmed",
            "comment_id": comment_id,
            "parent_author": parent_author,
            "confirmer": confirmer,
            "parent_new_flair": parent_result.get("new_flair"),
            "confirmer_new_flair": confirmer_result.get("new_flair"),
        }
