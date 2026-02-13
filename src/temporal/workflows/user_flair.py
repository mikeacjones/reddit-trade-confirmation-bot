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

        result = await workflow.execute_activity(
            flair_activities.increment_user_flair_atomic,
            args=[self._username, request_obj.delta],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=REDDIT_RETRY_POLICY,
        )

        result_obj = FlairIncrementResult(
            username=self._username,
            applied=True,
            old_count=result["old_count"],
            new_count=result["new_count"],
            old_flair=result.get("old_flair"),
            new_flair=result.get("new_flair"),
        )
        serialized = asdict(result_obj)
        self._results_by_request_id[request_obj.request_id] = serialized
        return serialized
