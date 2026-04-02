"""Temporal bridge activities for cross-workflow operations."""

from temporalio import activity
from temporalio.client import Client, WithStartWorkflowOperation
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.service import RPCError

from ..workflows.comment_processing import CommentPollingWorkflow
from ..workflows.flair_coordinator import FlairCoordinatorWorkflow

from ..shared import (
    ActiveSubmissions,
    FlairIncrementRequest,
    FlairIncrementResult,
    SUBREDDIT_NAME,
    TASK_QUEUE,
)

_temporal_client: Client | None = None


def set_temporal_client(client: Client) -> None:
    """Inject the worker's Temporal client for use by bridge activities."""
    global _temporal_client
    _temporal_client = client


def _get_temporal_client() -> Client:
    """Get the shared Temporal client (must be set via set_temporal_client first)."""
    if _temporal_client is None:
        raise RuntimeError("Temporal client not set - call set_temporal_client() from the worker before starting")
    return _temporal_client


@activity.defn
async def request_flair_increment(request: FlairIncrementRequest) -> FlairIncrementResult:
    """Route increment requests through the centralized coordinator workflow."""
    client = _get_temporal_client()

    start_op = WithStartWorkflowOperation(
        FlairCoordinatorWorkflow.run,
        id=f"flair-coordinator-{SUBREDDIT_NAME}",
        task_queue=TASK_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )

    return await client.execute_update_with_start_workflow(
        FlairCoordinatorWorkflow.apply_increment,
        request,
        start_workflow_operation=start_op,
    )


@activity.defn
async def query_polling_submissions() -> ActiveSubmissions:
    """Query the polling workflow for its current/previous submission IDs.

    Returns ActiveSubmissions(None, None) if the polling workflow is not running.
    """
    client = _get_temporal_client()

    try:
        handle = client.get_workflow_handle(f"poll-{SUBREDDIT_NAME}")
        result = await handle.query(CommentPollingWorkflow.get_submission_ids)
        return ActiveSubmissions(
            current_submission_id=result.get("current_submission_id"),
            previous_submission_id=result.get("previous_submission_id"),
        )
    except RPCError:
        activity.logger.warning("Polling workflow not reachable, returning empty submissions")
        return ActiveSubmissions()
