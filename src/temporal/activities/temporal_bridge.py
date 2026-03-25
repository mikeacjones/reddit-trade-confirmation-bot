"""Temporal bridge activities for cross-workflow operations."""

import os

from temporalio import activity
from temporalio.client import Client, WithStartWorkflowOperation
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from src.temporal.workflows.flair_coordinator import FlairCoordinatorWorkflow

from ..shared import SUBREDDIT_NAME, TASK_QUEUE, FlairIncrementRequest, FlairIncrementResult, StartConfirmationInput

_temporal_client: Client | None = None


async def _get_temporal_client() -> Client:
    """Get or create a shared Temporal client for bridge activities."""
    global _temporal_client
    if _temporal_client is None:
        temporal_host = os.getenv("TEMPORAL_HOST", "localhost:7233")
        _temporal_client = await Client.connect(temporal_host)
    return _temporal_client


@activity.defn
async def request_flair_increment(request: FlairIncrementRequest) -> FlairIncrementResult:
    """Route increment requests through the centralized coordinator workflow."""
    client = await _get_temporal_client()

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
async def start_confirmation_workflow(input: StartConfirmationInput) -> bool:
    """Start independent confirmation workflow; return False if already running."""
    client = await _get_temporal_client()

    try:
        await client.start_workflow(
            "ProcessConfirmationWorkflow",
            args=[input.comment_data],
            id=input.workflow_id,
            task_queue=TASK_QUEUE,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
        )
        return True
    except WorkflowAlreadyStartedError:
        return False
