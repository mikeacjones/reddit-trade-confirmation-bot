"""Comment processing workflows for trade confirmation."""

import asyncio
from datetime import datetime, timedelta, timezone

from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, WorkflowAlreadyStartedError
from temporalio.workflow import ContinueAsNewVersioningBehavior, ParentClosePolicy

from bot.config import SUBREDDIT_NAME, TASK_QUEUE
from bot.models import (
    CommentData,
    FetchCommentsInput,
    FlairIncrementResult,
    ReplyToCommentInput,
)
from bot.services import ConfirmationService

from ..activities import comments as comment_activities
from ..activities import notifications as notification_activities
from ..activities import submissions as submission_activities
from ..shared import (
    PUSHOVER_RETRY_POLICY,
    REDDIT_RETRY_POLICY,
    WATERMARK_IDS_MAX,
)


@workflow.defn
class CommentPollingWorkflow:
    """Continuously polls for new comments and processes them.

    Delegates polling to a long-running activity that loops internally,
    heartbeating and sleeping between iterations.  The activity only returns
    when actionable comments are found (or a watermark gap is detected).

    Tracks current and previous month submission IDs in workflow state.
    These are set via the ``set_current_submission`` signal (sent by
    ``MonthlyPostWorkflow``) and passed to the polling activity so it
    knows which submissions to scan.

    The workflow remains responsive to signals (stop, set_current_submission)
    by using ``start_activity`` + ``wait_condition``.  When a signal arrives
    the running activity is cancelled and restarted with updated state.
    """

    def __init__(self):
        self._should_stop = False
        self._seen_ids: list[str] = []
        self._processed_count = 0
        self._gap_alerted = False
        self._current_submission_id: str | None = None
        self._previous_submission_id: str | None = None
        self._submission_changed = False

    @workflow.signal
    def stop(self) -> None:
        """Signal to stop the polling loop."""
        self._should_stop = True

    @workflow.signal
    def wake_up(self) -> None:
        pass

    @workflow.signal
    def set_current_submission(self, submission_id: str) -> None:
        """Signal that a new monthly submission has been created.

        Shifts the current submission to previous and sets the new one.
        Triggers the polling activity to restart with updated submission IDs.
        """
        self._previous_submission_id = self._current_submission_id
        self._current_submission_id = submission_id
        self._submission_changed = True

    @workflow.query
    def get_status(self) -> dict[str, str | int | None]:
        """Query the current status of the polling workflow."""
        return {
            "last_seen_id": self._seen_ids[0] if self._seen_ids else None,
            "processed_count": self._processed_count,
            "running": not self._should_stop,
            "seen_ids_count": len(self._seen_ids),
        }

    @workflow.query
    def get_submission_ids(self) -> dict[str, str | None]:
        """Query the current and previous submission IDs."""
        return {
            "current_submission_id": self._current_submission_id,
            "previous_submission_id": self._previous_submission_id,
        }

    @workflow.run
    async def run(
        self,
        seen_ids: list[str] | None = None,
        current_submission_id: str | None = None,
        previous_submission_id: str | None = None,
    ) -> dict[str, str | int | None]:
        """Run the comment polling loop.

        Args:
            seen_ids: Recently-seen comment IDs (carried over from continue-as-new).
            current_submission_id: Current month's submission (from continue-as-new).
            previous_submission_id: Previous month's submission (from continue-as-new).

        Returns:
            Final status when stopped.
        """
        self._seen_ids = seen_ids or []
        self._current_submission_id = current_submission_id
        self._previous_submission_id = previous_submission_id
        workflow.logger.info("Starting comment polling for subreddit")

        # Bootstrap: discover submission IDs from Reddit on first run.
        if self._current_submission_id is None:
            result = await workflow.execute_activity(
                submission_activities.fetch_active_submission_ids,
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=REDDIT_RETRY_POLICY,
            )
            self._current_submission_id = result.current_submission_id
            self._previous_submission_id = result.previous_submission_id
            workflow.logger.info(
                "Bootstrapped submissions: current=%s, previous=%s",
                self._current_submission_id,
                self._previous_submission_id,
            )

        while not self._should_stop:
            if (
                workflow.info().is_continue_as_new_suggested()
                or workflow.info().is_target_worker_deployment_version_changed()
            ):
                workflow.logger.info("Continuing as new")
                workflow.continue_as_new(
                    args=[
                        self._seen_ids,
                        self._current_submission_id,
                        self._previous_submission_id,
                    ],
                    initial_versioning_behavior=ContinueAsNewVersioningBehavior.AUTO_UPGRADE,
                )

            self._submission_changed = False

            # Build list of active submission IDs (filtering None).
            active_submission_ids = [
                sid
                for sid in [self._current_submission_id, self._previous_submission_id]
                if sid is not None
            ]

            # Start long-running poll activity (non-blocking).
            activity_handle = workflow.start_activity(
                comment_activities.poll_new_comments,
                args=[
                    FetchCommentsInput(
                        seen_ids=self._seen_ids,
                        active_submission_ids=active_submission_ids,
                        current_submission_id=self._current_submission_id or "",
                    )
                ],
                start_to_close_timeout=timedelta(hours=24),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            # Wait for activity completion OR a signal.
            await workflow.wait_condition(
                lambda: (
                    activity_handle.done()
                    or self._should_stop
                    or self._submission_changed
                    or workflow.info().is_target_worker_deployment_version_changed()
                )
            )

            if not activity_handle.done():
                # A signal arrived before the activity finished — cancel and
                # let the loop re-evaluate (stop or restart with new IDs).
                activity_handle.cancel()
                try:
                    await activity_handle
                except (asyncio.CancelledError, ActivityError):
                    pass
                continue

            # Activity completed — process results.
            poll_result = await activity_handle

            comments = poll_result.comments

            # Prepend new IDs to workflow state, truncate to watermark size.
            if poll_result.scanned_ids:
                self._seen_ids = (poll_result.scanned_ids + self._seen_ids)[
                    :WATERMARK_IDS_MAX
                ]

            if poll_result.possible_gap:
                workflow.logger.warning(
                    "Possible listing gap for r/%s: scanned=%s without finding any seen comment",
                    SUBREDDIT_NAME,
                    poll_result.scanned_count,
                )
                if not self._gap_alerted:
                    await workflow.execute_activity(
                        notification_activities.send_pushover_notification,
                        args=[
                            (
                                f"[r/{SUBREDDIT_NAME}] Possible comment listing gap: "
                                f"scanned {poll_result.scanned_count} comments without finding "
                                "any previously-seen comment. "
                                "Manual review of recent confirmations recommended."
                            )
                        ],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=PUSHOVER_RETRY_POLICY,
                    )
                    self._gap_alerted = True
            elif poll_result.found_seen:
                self._gap_alerted = False

            # Route comments: reject root comments on old threads, process
            # non-root confirming comments on current or previous.
            for comment_data in comments:
                # Root comment on previous submission → wrong thread rejection.
                if (
                    comment_data.is_root
                    and comment_data.submission_id == self._previous_submission_id
                ):
                    await workflow.execute_activity(
                        comment_activities.reply_to_comment,
                        args=[
                            ReplyToCommentInput(
                                comment_id=comment_data.id,
                                template_name="old_confirmation_thread",
                            )
                        ],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=REDDIT_RETRY_POLICY,
                    )
                    self._processed_count += 1
                    continue

                # Non-root confirming comment → process via child workflow.
                workflow_id = f"process-{comment_data.id}"

                try:
                    await workflow.start_child_workflow(
                        ProcessConfirmationWorkflow.run,
                        comment_data,
                        id=workflow_id,
                        task_queue=TASK_QUEUE,
                        parent_close_policy=ParentClosePolicy.ABANDON,
                        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
                    )
                    self._processed_count += 1
                except WorkflowAlreadyStartedError:
                    workflow.logger.warning(
                        "Comment %s already has workflow %s, skipping start",
                        comment_data.id,
                        workflow_id,
                    )

        workflow.logger.info("Comment polling stopped")
        return self.get_status()


@workflow.defn
class ProcessConfirmationWorkflow:
    """Process a single comment for potential trade confirmation."""

    @staticmethod
    async def _save(comment_id: str) -> None:
        """Mark a comment as saved/processed."""
        await workflow.execute_activity(
            comment_activities.mark_comment_saved,
            args=[comment_id],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=REDDIT_RETRY_POLICY,
        )

    @workflow.run
    async def run(self, comment_data: CommentData) -> dict:
        """Process a potential trade confirmation comment."""
        comment_id = comment_data.id
        author = comment_data.author_name

        workflow.logger.info("Processing comment %s by %s", comment_id, author)

        try:
            # Validate the confirmation
            validation = await workflow.execute_activity(
                comment_activities.validate_confirmation,
                args=[comment_data],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            # Handle validation failure with template response
            if not validation.valid:
                reply_input = ConfirmationService.build_invalid_reply(
                    comment_data, validation
                )
                if reply_input is not None:
                    await workflow.execute_activity(
                        comment_activities.reply_to_comment,
                        args=[reply_input],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=REDDIT_RETRY_POLICY,
                    )
                    await self._save(comment_id)
                    return {
                        "status": "rejected",
                        "reason": validation.reason,
                        "comment_id": comment_id,
                    }

                await self._save(comment_id)
                return {"status": "skipped", "comment_id": comment_id}

            # Valid confirmation - request coordinated flair updates
            parent_comment_id = validation.parent_comment_id
            parent_request, confirmer_request = (
                ConfirmationService.build_flair_increment_requests(validation)
            )

            parent_increment = workflow.start_activity(
                "request_flair_increment",
                args=[parent_request],
                result_type=FlairIncrementResult,
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            confirmer_increment = workflow.start_activity(
                "request_flair_increment",
                args=[confirmer_request],
                result_type=FlairIncrementResult,
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            parent_result = await parent_increment
            confirmer_result = await confirmer_increment

            if parent_comment_id:
                await self._save(parent_comment_id)

            await workflow.execute_activity(
                comment_activities.reply_to_comment,
                args=[
                    ConfirmationService.build_confirmation_reply(
                        comment_id,
                        validation,
                        parent_result,
                        confirmer_result,
                    )
                ],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            # Save confirming comment only after successful confirmation flow.
            await self._save(comment_id)

            elapsed = workflow.now() - datetime.fromtimestamp(
                comment_data.created_utc, tz=timezone.utc
            )
            workflow.logger.info(
                "Confirmed trade: %s (%s) <-> %s (%s) — %.1fs from comment to reply",
                validation.parent_author,
                parent_result.new_flair,
                validation.confirmer,
                confirmer_result.new_flair,
                elapsed.total_seconds(),
            )

            return ConfirmationService.build_confirmed_result(
                comment_id,
                validation,
                parent_result,
                confirmer_result,
            )
        except Exception as exc:
            error_type = type(exc).__name__
            error_message = str(exc)
            workflow.logger.error(
                "Manual review required for comment %s by %s: %s: %s",
                comment_id,
                author,
                error_type,
                error_message,
            )

            await workflow.execute_activity(
                notification_activities.send_pushover_notification,
                args=[
                    (
                        f"[r/{SUBREDDIT_NAME}] Manual review required for "
                        f"comment {comment_id} by u/{author}: {error_type}: "
                        f"{error_message}"
                    )
                ],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=PUSHOVER_RETRY_POLICY,
            )

            raise
