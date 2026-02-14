"""Per-user flair workflow to serialize increments."""

from dataclasses import asdict
from datetime import timedelta

from temporalio import workflow

from ..activities import flair as flair_activities
from ..shared import FlairIncrementRequest, FlairIncrementResult, REDDIT_RETRY_POLICY


@workflow.defn
class UserFlairWorkflow:
    """Serializes flair updates for a single user.

    A workflow instance is keyed by username via workflow ID `flair-user-{username}`.
    Increment requests are applied via update calls with idempotency keys.
    """

    def __init__(self) -> None:
        self._username: str | None = None
        self._results_by_request_id: dict[str, dict] = {}
        self._update_in_progress = False

    @workflow.run
    async def run(self, username: str) -> None:
        """Run indefinitely and serve update requests."""
        self._username = username
        await workflow.wait_condition(lambda: False)

    @workflow.update
    async def apply_increment(self, request: dict) -> dict:
        """Apply an increment request exactly once per request ID."""
        request_obj = FlairIncrementRequest(**request)

        if self._username is None:
            raise RuntimeError("Workflow not initialized with username")

        if request_obj.request_id in self._results_by_request_id:
            return self._results_by_request_id[request_obj.request_id]

        # Update handlers can overlap at await points. Enforce strict in-workflow
        # serialization so each user's increment requests are processed one-at-a-time.
        while self._update_in_progress:
            await workflow.wait_condition(lambda: not self._update_in_progress)

        self._update_in_progress = True
        try:
            # Re-check after acquiring the lock in case a concurrent handler
            # completed this same request while we were waiting.
            if request_obj.request_id in self._results_by_request_id:
                return self._results_by_request_id[request_obj.request_id]

            current = await workflow.execute_activity(
                flair_activities.get_user_flair,
                args=[self._username],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            target_count = current["trade_count"] + request_obj.delta
            set_result = await workflow.execute_activity(
                flair_activities.set_user_flair,
                args=[self._username, target_count],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            result_obj = FlairIncrementResult(
                username=self._username,
                applied=True,
                old_count=current["trade_count"],
                new_count=target_count,
                old_flair=current.get("flair_text"),
                new_flair=set_result.get("new_flair"),
            )
            serialized = asdict(result_obj)
            self._results_by_request_id[request_obj.request_id] = serialized
            return serialized
        finally:
            self._update_in_progress = False
