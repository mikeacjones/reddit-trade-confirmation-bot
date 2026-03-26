"""Comment processing workflows for trade confirmation."""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from temporalio import workflow
from temporalio.workflow import ContinueAsNewVersioningBehavior

from ..activities import comments as comment_activities
from ..activities import notifications as notification_activities
from ..activities import temporal_bridge as bridge_activities
from ..shared import (
    REDDIT_RETRY_POLICY,
    SUBREDDIT_NAME,
    CommentData,
    FetchCommentsInput,
    FlairIncrementRequest,
    ReplyToCommentInput,
    StartConfirmationInput,
)


def _iter_exception_chain(exc: BaseException):
    """Iterate exception, __cause__/__context__, and Temporal-style .cause chains."""
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        yield current
        seen.add(id(current))
        next_exc = (
            getattr(current, "cause", None) or current.__cause__ or current.__context__
        )
        current = next_exc if isinstance(next_exc, BaseException) else None


def _is_control_flow_stop_exception(exc: Exception) -> bool:
    """Return True for cancellation/termination-style exceptions."""
    for err in _iter_exception_chain(exc):
        err_type = type(err).__name__.lower()
        if "cancel" in err_type or "terminate" in err_type:
            return True

    return False


@workflow.defn
class CommentPollingWorkflow:
    """Continuously polls for new comments and processes them.

    This workflow runs indefinitely, periodically checking for new comments
    in the specified submission and starting independent workflows to process them.

    Uses adaptive polling inspired by PRAW's stream_generator: starts at 1 second,
    doubles on each empty response up to 16 seconds, and resets to 1 second when
    new comments are found.
    """

    # Continue-as-new after this many iterations to prevent unbounded event history
    MAX_ITERATIONS = 500
    # Alert when we scan deep into the listing and still cannot find the watermark.
    WATERMARK_GAP_SCAN_THRESHOLD = 900
    # Adaptive polling bounds (seconds)
    MIN_POLL_DELAY = 1.0
    MAX_POLL_DELAY = 3.0

    def __init__(self):
        self._should_stop = False
        self._seen_ids: list[str] = []
        self._processed_count = 0
        self._last_gap_alert_seen_ids_len: int = 0
        self._poll_delay: float = self.MIN_POLL_DELAY
        self._refresh_submissions = False

    @workflow.signal
    def stop(self) -> None:
        """Signal to stop the polling loop."""
        self._should_stop = True

    @workflow.signal
    def invalidate_submissions(self) -> None:
        """Signal to refresh the bot submissions cache on the next poll."""
        self._refresh_submissions = True

    @workflow.query
    def get_status(self) -> dict:
        """Query the current status of the polling workflow."""
        return {
            "last_seen_id": self._seen_ids[0] if self._seen_ids else None,
            "processed_count": self._processed_count,
            "running": not self._should_stop,
            "poll_delay": self._poll_delay,
            "seen_ids_count": len(self._seen_ids),
        }

    @workflow.run
    async def run(
        self,
        seen_ids: list[str] | None = None,
        poll_delay: float = MIN_POLL_DELAY,
    ) -> dict:
        """Run the comment polling loop.

        Args:
            seen_ids: Recently-seen comment IDs (carried over from continue-as-new).
            poll_delay: Current adaptive poll delay (carried over from continue-as-new).

        Returns:
            Final status when stopped.
        """
        self._seen_ids = seen_ids or []
        self._poll_delay = poll_delay
        workflow.logger.info("Starting comment polling for subreddit")

        while not self._should_stop:
            had_seen_ids = len(self._seen_ids) > 0
            refresh = self._refresh_submissions
            self._refresh_submissions = False
            poll_result = await workflow.execute_activity(
                comment_activities.fetch_new_comments,
                args=[
                    FetchCommentsInput(
                        seen_ids=self._seen_ids,
                        refresh_submissions=refresh,
                    )
                ],
                start_to_close_timeout=timedelta(seconds=120),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            comments = poll_result.comments
            found_seen = poll_result.found_seen
            listing_exhausted = poll_result.listing_exhausted
            scanned_count = poll_result.scanned_count
            updated_seen_ids = poll_result.seen_ids

            # Update seen_ids from activity result.
            if updated_seen_ids:
                self._seen_ids = updated_seen_ids

            possible_gap = (
                had_seen_ids
                and not found_seen
                and listing_exhausted
                and scanned_count >= self.WATERMARK_GAP_SCAN_THRESHOLD
            )
            if possible_gap:
                workflow.logger.warning(
                    "Possible listing gap for r/%s: scanned=%s without finding any seen comment",
                    SUBREDDIT_NAME,
                    scanned_count,
                )
                # Only alert once per gap (use seen_ids length as a rough dedup key).
                if self._last_gap_alert_seen_ids_len != len(self._seen_ids):
                    await workflow.execute_activity(
                        notification_activities.send_pushover_notification,
                        args=[
                            (
                                f"[r/{SUBREDDIT_NAME}] Possible comment listing gap: "
                                f"scanned {scanned_count} comments without finding "
                                "any previously-seen comment. "
                                "Manual review of recent confirmations recommended."
                            )
                        ],
                        start_to_close_timeout=timedelta(seconds=30),
                    )
                    self._last_gap_alert_seen_ids_len = len(self._seen_ids)
            elif found_seen:
                self._last_gap_alert_seen_ids_len = 0

            # Process each comment via independently-started workflow
            for comment_data in comments:
                # Use comment ID as workflow ID for idempotency
                workflow_id = f"process-{comment_data.id}"

                started = await workflow.execute_activity(
                    bridge_activities.start_confirmation_workflow,
                    args=[
                        StartConfirmationInput(
                            workflow_id=workflow_id,
                            comment_data=comment_data,
                        )
                    ],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=REDDIT_RETRY_POLICY,
                )
                if started:
                    self._processed_count += 1
                else:
                    workflow.logger.warning(
                        "Comment %s already has workflow %s, skipping start",
                        comment_data.id,
                        workflow_id,
                    )

            # Adaptive delay: reset on activity, back off when idle
            if comments:
                self._poll_delay = self.MIN_POLL_DELAY
            else:
                self._poll_delay = min(self._poll_delay * 2, self.MAX_POLL_DELAY)

            # Wait before next poll (durable timer - survives worker restarts)
            await workflow.sleep(timedelta(seconds=self._poll_delay))

            if workflow.info().is_continue_as_new_suggested():
                workflow.logger.info("Continuing as new after")
                workflow.continue_as_new(
                    args=[self._seen_ids, self._poll_delay],
                    initial_versioning_behavior=ContinueAsNewVersioningBehavior.AUTO_UPGRADE,
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
                reason = validation.reason
                if reason:
                    await workflow.execute_activity(
                        comment_activities.reply_to_comment,
                        args=[
                            ReplyToCommentInput(
                                comment_id=comment_id,
                                template_name=reason,
                                format_args={
                                    **asdict(comment_data),
                                    "parent_author": validation.parent_author,
                                    "parent_comment_id": validation.parent_comment_id,
                                },
                            )
                        ],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=REDDIT_RETRY_POLICY,
                    )
                    await self._save(comment_id)
                    return {
                        "status": "rejected",
                        "reason": reason,
                        "comment_id": comment_id,
                    }

                await self._save(comment_id)
                return {"status": "skipped", "comment_id": comment_id}

            # Valid confirmation - request coordinated flair updates
            parent_author = validation.parent_author
            confirmer = validation.confirmer
            assert parent_author is not None and confirmer is not None

            parent_comment_id = validation.parent_comment_id
            confirmation_key = f"{parent_comment_id}:{confirmer}".lower()

            parent_increment = workflow.start_activity(
                bridge_activities.request_flair_increment,
                args=[
                    FlairIncrementRequest(
                        username=parent_author,
                        request_id=f"{confirmation_key}:parent",
                    )
                ],
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            confirmer_increment = workflow.start_activity(
                bridge_activities.request_flair_increment,
                args=[
                    FlairIncrementRequest(
                        username=confirmer,
                        request_id=f"{confirmation_key}:confirmer",
                    )
                ],
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            parent_result = await parent_increment
            confirmer_result = await confirmer_increment

            if parent_comment_id:
                await self._save(parent_comment_id)

            # Use reply_to_comment_id if available (for mod approvals), otherwise comment_id
            reply_comment_id = validation.reply_to_comment_id or comment_id
            await workflow.execute_activity(
                comment_activities.reply_to_comment,
                args=[
                    ReplyToCommentInput(
                        comment_id=reply_comment_id,
                        template_name="trade_confirmation",
                        format_args={
                            "comment_id": reply_comment_id,
                            "confirmer": confirmer,
                            "parent_author": parent_author,
                            "old_comment_flair": confirmer_result.old_flair
                            or "unknown",
                            "new_comment_flair": confirmer_result.new_flair
                            or "unknown",
                            "old_parent_flair": parent_result.old_flair or "unknown",
                            "new_parent_flair": parent_result.new_flair or "unknown",
                        },
                    )
                ],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            # Save confirming comment only after successful confirmation flow.
            await self._save(comment_id)

            comment_created = datetime.fromtimestamp(
                comment_data.created_utc, tz=timezone.utc
            )
            elapsed = workflow.now() - comment_created
            workflow.logger.info(
                "Confirmed trade: %s (%s) <-> %s (%s) — %.1fs from comment to reply",
                parent_author,
                parent_result.new_flair,
                confirmer,
                confirmer_result.new_flair,
                elapsed.total_seconds(),
            )

            return {
                "status": "confirmed",
                "comment_id": comment_id,
                "parent_author": parent_author,
                "confirmer": confirmer,
                "parent_new_flair": parent_result.new_flair,
                "confirmer_new_flair": confirmer_result.new_flair,
            }
        except Exception as exc:
            if _is_control_flow_stop_exception(exc):
                workflow.logger.info(
                    "Propagating cancellation/termination for comment %s (%s)",
                    comment_id,
                    type(exc).__name__,
                )
                raise

            error_type = type(exc).__name__
            error_message = str(exc)
            workflow.logger.error(
                "Manual review required for comment %s by %s: %s: %s",
                comment_id,
                author,
                error_type,
                error_message,
            )

            try:
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
                )
            except Exception as notify_exc:
                workflow.logger.warning(
                    "Failed to send manual review notification for comment %s: %s: %s",
                    comment_id,
                    type(notify_exc).__name__,
                    str(notify_exc),
                )

            try:
                await self._save(comment_id)
            except Exception as save_exc:
                workflow.logger.warning(
                    "Failed to save manual-review comment %s: %s: %s",
                    comment_id,
                    type(save_exc).__name__,
                    str(save_exc),
                )

            return {
                "status": "manual_review_required",
                "comment_id": comment_id,
                "author": author,
                "error_type": error_type,
                "error": error_message,
            }
