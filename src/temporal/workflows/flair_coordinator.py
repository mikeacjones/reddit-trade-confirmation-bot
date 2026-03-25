"""Centralized workflow to coordinate flair increments."""

from collections import OrderedDict
from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ApplicationError

from ..activities import flair as flair_activities
from ..shared import (
    REDDIT_RETRY_POLICY,
    FlairIncrementRequest,
    FlairIncrementResult,
    SetUserFlairInput,
)


@workflow.defn
class FlairCoordinatorWorkflow:
    """Serializes flair increments per-user and deduplicates request IDs."""

    MAX_APPLIED_BEFORE_CONTINUE_AS_NEW = 500
    MAX_FLAIR_CACHE = 30

    def __init__(self) -> None:
        self._results_by_request_id: dict[str, FlairIncrementResult] = {}
        self._users_in_progress: set[str] = set()
        self._applied_count = 0
        self._should_continue_as_new = False
        self._draining = False
        # LRU cache of last-set flair counts per user.  Reddit's flair read API
        # (GET flairlist) is eventually consistent and can return stale data
        # immediately after a write (POST flair).  Because all increments are
        # serialised through this workflow, our own bookkeeping is authoritative.
        self._last_known_count: OrderedDict[str, int] = OrderedDict()

    def _record_result(self, request_id: str, result: FlairIncrementResult) -> None:
        """Store a result and flag continue-as-new if needed."""
        self._results_by_request_id[request_id] = result
        self._applied_count += 1
        if self._applied_count >= self.MAX_APPLIED_BEFORE_CONTINUE_AS_NEW:
            self._should_continue_as_new = True

    @workflow.run
    async def run(
        self,
        carried_flair_counts: dict[str, int] | None = None,
    ) -> None:
        """Run indefinitely until continue-as-new rollover is requested."""
        if carried_flair_counts:
            self._last_known_count = OrderedDict(carried_flair_counts)

        await workflow.wait_condition(
            lambda: workflow.info().is_continue_as_new_suggested()
        )
        self._draining = True
        await workflow.wait_condition(workflow.all_handlers_finished)

        workflow.continue_as_new(args=[dict(self._last_known_count)])

    @workflow.update
    async def apply_increment(self, req: FlairIncrementRequest) -> FlairIncrementResult:
        """Apply one increment request with per-user serialization."""
        cached = self._results_by_request_id.get(req.request_id)
        if cached is not None:
            return cached

        username = req.username
        while username in self._users_in_progress:
            await workflow.wait_condition(
                lambda u=username: u not in self._users_in_progress
            )

        self._users_in_progress.add(username)
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

            api_count = current.trade_count
            is_trade_tracked = current.is_trade_tracked

            # Preserve non-trade custom flairs rather than coercing them to 0.
            if not is_trade_tracked or not isinstance(api_count, int):
                result = FlairIncrementResult(
                    username=req.username,
                    applied=False,
                    old_count=api_count if isinstance(api_count, int) else None,
                    new_count=api_count if isinstance(api_count, int) else None,
                    old_flair=current.flair_text,
                    new_flair=current.flair_text,
                )
                self._record_result(req.request_id, result)
                return result

            # Use our cached count when it's ahead of what Reddit returned,
            # since the read API can lag behind the write API.
            cached_count = self._last_known_count.get(req.username)
            if cached_count is not None and cached_count > api_count:
                current_count = cached_count
            else:
                current_count = api_count

            target_count = current_count + req.delta
            set_result = await workflow.execute_activity(
                flair_activities.set_user_flair,
                args=[
                    SetUserFlairInput(
                        username=req.username,
                        new_count=target_count,
                        old_flair=current.flair_text,
                    )
                ],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=REDDIT_RETRY_POLICY,
            )

            self._last_known_count[req.username] = target_count
            self._last_known_count.move_to_end(req.username)
            while len(self._last_known_count) > self.MAX_FLAIR_CACHE:
                self._last_known_count.popitem(last=False)

            result = FlairIncrementResult(
                username=req.username,
                applied=True,
                old_count=current_count,
                new_count=target_count,
                old_flair=current.flair_text or "Trades: 0",
                new_flair=set_result.new_flair,
            )

            self._record_result(req.request_id, result)

            return result
        finally:
            self._users_in_progress.discard(username)

    @apply_increment.validator
    def validate_can_accept_increment(self, req: FlairIncrementRequest) -> None:
        if self._draining:
            raise ApplicationError("Workflow is draining for continue-as-new; retry")
