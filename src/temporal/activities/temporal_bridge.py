"""Temporal bridge activities for cross-workflow operations."""

import os

from temporalio import activity
from temporalio.client import Client, WithStartWorkflowOperation
from temporalio.common import WorkflowIDConflictPolicy

from ..shared import TASK_QUEUE

_temporal_client: Client | None = None


async def _get_temporal_client() -> Client:
    """Get or create a shared Temporal client for bridge activities."""
    global _temporal_client
    if _temporal_client is None:
        temporal_host = os.getenv("TEMPORAL_HOST", "localhost:7233")
        _temporal_client = await Client.connect(temporal_host)
    return _temporal_client


@activity.defn
async def request_user_flair_increment(username: str, request: dict) -> dict:
    """Apply a serialized flair increment for a user via Update-With-Start."""
    client = await _get_temporal_client()

    handle = WithStartWorkflowOperation(
        "UserFlairWorkflow",
        args=[username],
        id=f"flair-user-{username.lower()}",
        task_queue=TASK_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )

    return await client.execute_update_with_start_workflow(
        "apply_increment",
        request,
        start_workflow_operation=handle,
    )
