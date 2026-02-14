"""Centralized workflow to coordinate flair increments."""

from dataclasses import asdict
from datetime import timedelta

from temporalio import workflow

from ..activities import flair as flair_activities
from ..shared import FlairIncrementRequest, FlairIncrementResult, REDDIT_RETRY_POLICY


@workflow.defn
class FlairCoordinatorWorkflow:
    """Serializes flair increments and deduplicates request IDs globally."""

    MAX_APPLIED_BEFORE_CONTINUE_AS_NEW = 500
    MAX_DEDUPE_RESULTS = 2000

    def __init__(self) -> None:
        self._results_by_request_id: dict[str, dict] = {}
        self._update_in_progress = False
        self._applied_count = 0
        self._should_continue_as_new = False

    @workflow.run
    async def run(self, carried_results: list[dict] | None = None) -> None:
        """Run indefinitely until continue-as-new rollover is requested."""
        if carried_results:
            self._results_by_request_id = {
                item["request_id"]: item["result"] for item in carried_results
            }

        await workflow.wait_condition(lambda: self._should_continue_as_new)

        carried = [
            {"request_id": request_id, "result": result}
            for request_id, result in self._results_by_request_id.items()
        ]
        workflow.continue_as_new(args=[carried])

    @workflow.update
    async def apply_increment(self, request: dict) -> dict:
        """Apply one increment request with global one-at-a-time serialization."""
        req = FlairIncrementRequest(**request)

        cached = self._results_by_request_id.get(req.request_id)
        if cached is not None:
            return cached

        while self._update_in_progress:
            await workflow.wait_condition(lambda: not self._update_in_progress)

        self._update_in_progress = True
        try:
            cached = self._results_by_request_id.get(req.request_id)
            if cached is not None:
                return cached

            current = await workflow.execute_activity(
                flair_activities.get_user_flair,
                args=[req.username],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            current_count = current.get("trade_count")
            is_trade_tracked = current.get("is_trade_tracked", True)

            # Preserve non-trade custom flairs rather than coercing them to 0.
            if not is_trade_tracked or not isinstance(current_count, int):
                result = asdict(
                    FlairIncrementResult(
                        username=req.username,
                        applied=False,
                        old_count=current_count if isinstance(current_count, int) else None,
                        new_count=current_count if isinstance(current_count, int) else None,
                        old_flair=current.get("flair_text"),
                        new_flair=current.get("flair_text"),
                    )
                )
                self._results_by_request_id[req.request_id] = result
                self._applied_count += 1

                while len(self._results_by_request_id) > self.MAX_DEDUPE_RESULTS:
                    oldest_key = next(iter(self._results_by_request_id))
                    del self._results_by_request_id[oldest_key]

                if self._applied_count >= self.MAX_APPLIED_BEFORE_CONTINUE_AS_NEW:
                    self._should_continue_as_new = True
                return result

            target_count = current_count + req.delta
            set_result = await workflow.execute_activity(
                flair_activities.set_user_flair,
                args=[req.username, target_count, current.get("flair_text")],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            result = asdict(
                FlairIncrementResult(
                    username=req.username,
                    applied=True,
                    old_count=current_count,
                    new_count=target_count,
                    old_flair=current.get("flair_text"),
                    new_flair=set_result.get("new_flair"),
                )
            )

            self._results_by_request_id[req.request_id] = result
            self._applied_count += 1

            while len(self._results_by_request_id) > self.MAX_DEDUPE_RESULTS:
                oldest_key = next(iter(self._results_by_request_id))
                del self._results_by_request_id[oldest_key]

            if self._applied_count >= self.MAX_APPLIED_BEFORE_CONTINUE_AS_NEW:
                self._should_continue_as_new = True

            return result
        finally:
            self._update_in_progress = False
