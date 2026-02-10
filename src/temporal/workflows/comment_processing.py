"""Comment processing workflows for trade confirmation."""

from datetime import timedelta
from typing import Optional

from temporalio import workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from ..activities import comments as comment_activities
from ..activities import flair as flair_activities
from ..activities import notifications as notification_activities
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
    """Return True for cancellation/termination-style exceptions.

    These should propagate so Temporal records canceled/terminated semantics
    instead of converting them to manual review outcomes.
    """
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
    in the specified subreddit and spawning child workflows to process them.
    """

    # Continue-as-new after this many iterations to prevent unbounded event history
    MAX_ITERATIONS = 50
    # Reddit listings are typically capped near 1000 items; alert when we scan
    # deep into that window and still can't find our watermark.
    WATERMARK_GAP_SCAN_THRESHOLD = 900

    def __init__(self):
        self._should_stop = False
        self._last_seen_id: Optional[str] = None
        self._processed_count = 0
        self._iterations = 0
        self._last_gap_alert_for_watermark: Optional[str] = None
        self._reload_flair_metadata_requested = False

    @workflow.signal
    def stop(self) -> None:
        """Signal to stop the polling loop."""
        self._should_stop = True

    @workflow.signal
    def reload_flair_metadata(self) -> None:
        """Signal to reload cached flair templates and moderator list."""
        self._reload_flair_metadata_requested = True

    @workflow.query
    def get_status(self) -> dict:
        """Query the current status of the polling workflow."""
        return {
            "last_seen_id": self._last_seen_id,
            "processed_count": self._processed_count,
            "running": not self._should_stop,
            "last_gap_alert_for_watermark": self._last_gap_alert_for_watermark,
            "reload_flair_metadata_requested": self._reload_flair_metadata_requested,
        }

    async def _start_comment_child_workflow(self, comment_data: dict) -> None:
        """Start child workflow for a comment candidate."""
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

    async def _handle_reload_flair_metadata(self) -> None:
        """Reload flair metadata caches when requested via signal."""
        if not self._reload_flair_metadata_requested:
            return

        try:
            result = await workflow.execute_activity(
                flair_activities.reload_flair_metadata_cache,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=REDDIT_RETRY_POLICY,
            )
            self._reload_flair_metadata_requested = False
            workflow.logger.info(
                "Reloaded flair metadata cache: %s templates, %s moderators",
                result.get("templates"),
                result.get("moderators"),
            )
        except Exception as exc:
            workflow.logger.warning(
                "Failed to reload flair metadata cache (will retry next poll): %s: %s",
                type(exc).__name__,
                str(exc),
            )

    async def _run_poll_iteration(self) -> None:
        """Run one polling iteration with watermark-gap detection."""
        previous_last_seen_id = self._last_seen_id
        poll_result = await workflow.execute_activity(
            comment_activities.fetch_new_comments,
            args=[self._last_seen_id],
            start_to_close_timeout=timedelta(seconds=120),
            heartbeat_timeout=timedelta(seconds=60),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        comments = poll_result.get("comments", [])
        if not isinstance(comments, list):
            comments = []
        comments = [c for c in comments if isinstance(c, dict)]

        newest_seen_id = poll_result.get("newest_seen_id")
        if not isinstance(newest_seen_id, str):
            newest_seen_id = None

        watermark_found = bool(poll_result.get("watermark_found", True))
        listing_exhausted = bool(poll_result.get("listing_exhausted", False))
        scanned_count = poll_result.get("scanned_count", 0)
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

        if newest_seen_id and (
            self._last_seen_id is None or int(newest_seen_id, 36) > int(self._last_seen_id, 36)
        ):
            self._last_seen_id = newest_seen_id

        for comment_data in comments:
            await self._start_comment_child_workflow(comment_data)

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
            await self._handle_reload_flair_metadata()
            await self._run_poll_iteration()

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
        """Process a potential trade confirmation comment."""
        comment_id = comment_data["id"]
        author = comment_data["author_name"]

        workflow.logger.info(f"Processing comment {comment_id} by {author}")

        try:
            validation = await workflow.execute_activity(
                comment_activities.validate_confirmation,
                args=[comment_data],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            if not validation["valid"]:
                reason = validation.get("reason")
                if reason:
                    await workflow.execute_activity(
                        comment_activities.reply_to_comment,
                        args=[comment_id, reason, {"comment": comment_data}],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=REDDIT_RETRY_POLICY,
                    )
                    if reason == "old_confirmation_thread":
                        await workflow.execute_activity(
                            comment_activities.lock_comment,
                            args=[comment_id],
                            start_to_close_timeout=timedelta(seconds=30),
                            retry_policy=REDDIT_RETRY_POLICY,
                        )
                    return {
                        "status": "rejected",
                        "reason": reason,
                        "comment_id": comment_id,
                    }

                return {"status": "skipped", "comment_id": comment_id}

            parent_author = validation["parent_author"]
            confirmer = validation["confirmer"]
            parent_comment_id = validation.get("parent_comment_id")

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

            parent_is_trade_tracked = parent_flair.get("is_trade_tracked", True)
            parent_trade_count = parent_flair.get("trade_count")
            if parent_is_trade_tracked and isinstance(parent_trade_count, int):
                parent_new_count = parent_trade_count + 1
                parent_result = await workflow.execute_activity(
                    flair_activities.set_user_flair,
                    args=[parent_author, parent_new_count, parent_flair.get("flair_text")],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=REDDIT_RETRY_POLICY,
                )
            else:
                workflow.logger.info(
                    "Preserving non-trade custom flair for u/%s (no trade count update)",
                    parent_author,
                )
                parent_result = {
                    "username": parent_author,
                    "old_flair": parent_flair.get("flair_text"),
                    "new_flair": parent_flair.get("flair_text"),
                    "success": True,
                    "preserved_custom_flair": True,
                }

            confirmer_is_trade_tracked = confirmer_flair.get("is_trade_tracked", True)
            confirmer_trade_count = confirmer_flair.get("trade_count")
            if confirmer_is_trade_tracked and isinstance(confirmer_trade_count, int):
                confirmer_new_count = confirmer_trade_count + 1
                confirmer_result = await workflow.execute_activity(
                    flair_activities.set_user_flair,
                    args=[
                        confirmer,
                        confirmer_new_count,
                        confirmer_flair.get("flair_text"),
                    ],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=REDDIT_RETRY_POLICY,
                )
            else:
                workflow.logger.info(
                    "Preserving non-trade custom flair for u/%s (no trade count update)",
                    confirmer,
                )
                confirmer_result = {
                    "username": confirmer,
                    "old_flair": confirmer_flair.get("flair_text"),
                    "new_flair": confirmer_flair.get("flair_text"),
                    "success": True,
                    "preserved_custom_flair": True,
                }

            if parent_comment_id:
                await workflow.execute_activity(
                    comment_activities.mark_comment_saved,
                    args=[parent_comment_id],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=REDDIT_RETRY_POLICY,
                )

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
            except Exception as notification_exc:
                workflow.logger.error(
                    "Failed to send manual review alert for %s: %s: %s",
                    comment_id,
                    type(notification_exc).__name__,
                    str(notification_exc),
                )

            return {
                "status": "manual_review_required",
                "comment_id": comment_id,
                "author": author,
                "error_type": error_type,
                "error": error_message,
            }
