"""Comment processing workflows for trade confirmation."""

from datetime import timedelta
from typing import Optional

from temporalio import workflow

from ..activities import comments as comment_activities
from ..activities import notifications as notification_activities
from ..activities import temporal_bridge as bridge_activities
from ..shared import REDDIT_RETRY_POLICY, SUBREDDIT_NAME


def _iter_exception_chain(exc: BaseException):
    """Iterate exception, __cause__/__context__, and Temporal-style .cause chains."""
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        yield current
        seen.add(id(current))
        next_exc = (
            getattr(current, "cause", None)
            or current.__cause__
            or current.__context__
        )
        current = next_exc if isinstance(next_exc, BaseException) else None


def _is_control_flow_stop_exception(exc: Exception) -> bool:
    """Return True for cancellation/termination-style exceptions."""
    for err in _iter_exception_chain(exc):
        err_type = type(err).__name__.lower()
        if (
            "cancel" in err_type
            or "cancellation" in err_type
            or "terminate" in err_type
            or "terminated" in err_type
        ):
            return True

    return False


@workflow.defn
class CommentPollingWorkflow:
    """Continuously polls for new comments and processes them.

    This workflow runs indefinitely, periodically checking for new comments
    in the specified submission and starting independent workflows to process them.
    """

    # Continue-as-new after this many iterations to prevent unbounded event history
    MAX_ITERATIONS = 50
    # Alert when we scan deep into the listing and still cannot find the watermark.
    WATERMARK_GAP_SCAN_THRESHOLD = 900

    def __init__(self):
        self._should_stop = False
        self._last_seen_id: Optional[str] = None
        self._processed_count = 0
        self._iterations = 0
        self._last_gap_alert_for_watermark: Optional[str] = None

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
            "last_gap_alert_for_watermark": self._last_gap_alert_for_watermark,
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
            previous_last_seen_id = self._last_seen_id
            poll_result = await workflow.execute_activity(
                comment_activities.fetch_new_comments,
                args=[self._last_seen_id],
                start_to_close_timeout=timedelta(seconds=120),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            comments = poll_result.get("comments", []) if isinstance(poll_result, dict) else []
            newest_seen_id = (
                poll_result.get("newest_seen_id") if isinstance(poll_result, dict) else None
            )
            watermark_found = bool(poll_result.get("watermark_found", True)) if isinstance(poll_result, dict) else True
            listing_exhausted = bool(poll_result.get("listing_exhausted", False)) if isinstance(poll_result, dict) else False
            scanned_count = poll_result.get("scanned_count", 0) if isinstance(poll_result, dict) else 0
            if not isinstance(scanned_count, int):
                scanned_count = 0

            possible_watermark_gap = (
                previous_last_seen_id is not None
                and not watermark_found
                and listing_exhausted
                and scanned_count >= self.WATERMARK_GAP_SCAN_THRESHOLD
            )
            if possible_watermark_gap:
                workflow.logger.warning(
                    "Possible listing gap for r/%s: scanned=%s without finding watermark %s",
                    SUBREDDIT_NAME,
                    scanned_count,
                    previous_last_seen_id,
                )
                if self._last_gap_alert_for_watermark != previous_last_seen_id:
                    await workflow.execute_activity(
                        notification_activities.send_pushover_notification,
                        args=[
                            (
                                f"[r/{SUBREDDIT_NAME}] Possible comment listing gap: "
                                f"scanned {scanned_count} comments without finding "
                                f"watermark {previous_last_seen_id}. "
                                "Manual review of recent confirmations recommended."
                            )
                        ],
                        start_to_close_timeout=timedelta(seconds=30),
                    )
                    self._last_gap_alert_for_watermark = previous_last_seen_id
            elif watermark_found:
                self._last_gap_alert_for_watermark = None

            if (
                isinstance(newest_seen_id, str)
                and (
                    self._last_seen_id is None
                    or int(newest_seen_id, 36) > int(self._last_seen_id, 36)
                )
            ):
                self._last_seen_id = newest_seen_id

            # Process each comment via independently-started workflow
            for comment_data in comments:
                if not isinstance(comment_data, dict) or "id" not in comment_data:
                    continue

                # Use comment ID as workflow ID for idempotency
                workflow_id = f"process-{comment_data['id']}"

                started = await workflow.execute_activity(
                    bridge_activities.start_confirmation_workflow,
                    args=[workflow_id, comment_data],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=REDDIT_RETRY_POLICY,
                )
                if started:
                    self._processed_count += 1
                else:
                    workflow.logger.warning(
                        "Comment %s already has workflow %s, skipping start",
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
    """Process a single comment for potential trade confirmation."""

    @workflow.run
    async def run(self, comment_data: dict) -> dict:
        """Process a potential trade confirmation comment."""
        comment_id = comment_data["id"]
        author = comment_data["author_name"]

        workflow.logger.info(f"Processing comment {comment_id} by {author}")

        try:
            # Validate the confirmation
            validation = await workflow.execute_activity(
                comment_activities.validate_confirmation,
                args=[comment_data],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            # Handle validation failure with template response
            if not validation["valid"]:
                reason = validation.get("reason")
                if reason:
                    await workflow.execute_activity(
                        comment_activities.reply_to_comment,
                        args=[comment_id, reason, {"comment": comment_data}],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=REDDIT_RETRY_POLICY,
                    )
                    await workflow.execute_activity(
                        comment_activities.mark_comment_saved,
                        args=[comment_id],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=REDDIT_RETRY_POLICY,
                    )
                    return {
                        "status": "rejected",
                        "reason": reason,
                        "comment_id": comment_id,
                    }

                await workflow.execute_activity(
                    comment_activities.mark_comment_saved,
                    args=[comment_id],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=REDDIT_RETRY_POLICY,
                )
                return {"status": "skipped", "comment_id": comment_id}

            # Valid confirmation - request coordinated flair updates
            parent_author = validation["parent_author"]
            confirmer = validation["confirmer"]
            parent_comment_id = validation.get("parent_comment_id")
            confirmation_key = f"{parent_comment_id}:{confirmer}".lower()

            parent_increment = workflow.start_activity(
                bridge_activities.request_flair_increment,
                args=[
                    {
                        "username": parent_author,
                        "request_id": f"{confirmation_key}:parent",
                        "delta": 1,
                    }
                ],
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            confirmer_increment = workflow.start_activity(
                bridge_activities.request_flair_increment,
                args=[
                    {
                        "username": confirmer,
                        "request_id": f"{confirmation_key}:confirmer",
                        "delta": 1,
                    }
                ],
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            parent_result = await parent_increment
            confirmer_result = await confirmer_increment

            if parent_comment_id:
                await workflow.execute_activity(
                    comment_activities.mark_comment_saved,
                    args=[parent_comment_id],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=REDDIT_RETRY_POLICY,
                )

            # Use reply_to_comment_id if available (for mod approvals), otherwise comment_id
            reply_comment_id = validation.get("reply_to_comment_id") or comment_id
            await workflow.execute_activity(
                comment_activities.post_confirmation_reply,
                args=[
                    reply_comment_id,
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

            # Save confirming comment only after successful confirmation flow.
            await workflow.execute_activity(
                comment_activities.mark_comment_saved,
                args=[comment_id],
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
                await workflow.execute_activity(
                    comment_activities.mark_comment_saved,
                    args=[comment_id],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=REDDIT_RETRY_POLICY,
                )
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
