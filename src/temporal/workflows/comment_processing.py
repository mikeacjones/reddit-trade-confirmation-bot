"""Comment processing workflows for trade confirmation."""

from datetime import timedelta
from typing import Optional

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from ..activities import (
        fetch_new_comments,
        validate_confirmation,
        increment_user_flair,
        mark_comment_saved,
        reply_to_comment,
        post_confirmation_reply,
    )
    from ..shared import REDDIT_RETRY_POLICY


@workflow.defn
class CommentPollingWorkflow:
    """Continuously polls for new comments and processes them.

    This workflow runs indefinitely, periodically checking for new comments
    in the specified submission and spawning child workflows to process them.
    """

    def __init__(self):
        self._should_stop = False
        self._last_seen_id: Optional[str] = None
        self._processed_count = 0

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
    async def run(self, submission_id: str, poll_interval_seconds: int = 30) -> dict:
        """Run the comment polling loop.

        Args:
            submission_id: The Reddit submission ID to poll for comments.
            poll_interval_seconds: How often to poll for new comments.

        Returns:
            Final status when stopped.
        """
        workflow.logger.info(f"Starting comment polling for submission {submission_id}")

        while not self._should_stop:
            # Fetch new comments - let exceptions propagate after retries exhausted
            comments = await workflow.execute_activity(
                fetch_new_comments,
                args=[submission_id, self._last_seen_id],
                start_to_close_timeout=timedelta(seconds=120),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            # Process each comment via child workflow
            for comment_data in comments:
                # Use comment ID as workflow ID for idempotency
                # If we crash and restart, already-processed comments won't be reprocessed
                workflow_id = f"process-{comment_data['id']}"

                await workflow.execute_child_workflow(
                    ProcessConfirmationWorkflow.run,
                    args=[comment_data],
                    id=workflow_id,
                )
                self._processed_count += 1
                self._last_seen_id = comment_data["id"]

            # Wait before next poll (durable timer - survives worker restarts)
            await workflow.sleep(timedelta(seconds=poll_interval_seconds))

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
            validate_confirmation,
            args=[comment_data],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        # Mark as processed regardless of outcome
        await workflow.execute_activity(
            mark_comment_saved,
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
                    reply_to_comment,
                    args=[comment_id, reason, {"comment": comment_data}],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=REDDIT_RETRY_POLICY,
                )
                return {"status": "rejected", "reason": reason, "comment_id": comment_id}

            return {"status": "skipped", "comment_id": comment_id}

        # Valid confirmation - update flairs
        parent_author = validation["parent_author"]
        confirmer = validation["confirmer"]
        parent_comment_id = validation.get("parent_comment_id")

        # Atomically increment both users' flairs
        # Each activity reads current flair and increments in one operation
        parent_result = await workflow.execute_activity(
            increment_user_flair,
            args=[parent_author],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        confirmer_result = await workflow.execute_activity(
            increment_user_flair,
            args=[confirmer],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        # Mark parent comment as saved too
        if parent_comment_id:
            await workflow.execute_activity(
                mark_comment_saved,
                args=[parent_comment_id],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=REDDIT_RETRY_POLICY,
            )

        # Post confirmation reply
        await workflow.execute_activity(
            post_confirmation_reply,
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


