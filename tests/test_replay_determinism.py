"""Replay determinism tests for all Temporal workflows.

Replays workflow histories from JSON files against the current workflow code.
If replay fails, the workflow code has introduced a non-deterministic change
that would break in-flight workflow executions.

History files are expected in the directory specified by the
REPLAY_HISTORIES_DIR environment variable (defaults to ./replay_histories).
Each file should be a JSON workflow history as produced by:
    temporal workflow show --output json > history.json
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Required by bot.config at import time
os.environ.setdefault("SUBREDDIT_NAME", "Pen_Swap")

from temporalio.client import WorkflowHistory  # noqa: E402
from temporalio.worker import Replayer  # noqa: E402
from temporalio.worker.workflow_sandbox import (  # noqa: E402
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)

from temporal.workflows.comment_processing import (  # noqa: E402
    CommentPollingWorkflow,
    ProcessConfirmationWorkflow,
)
from temporal.workflows.flair_coordinator import FlairCoordinatorWorkflow  # noqa: E402
from temporal.workflows.monthly_post import MonthlyPostWorkflow  # noqa: E402

ALL_WORKFLOWS = [
    CommentPollingWorkflow,
    ProcessConfirmationWorkflow,
    FlairCoordinatorWorkflow,
    MonthlyPostWorkflow,
]

HISTORIES_DIR = Path(
    os.environ.get("REPLAY_HISTORIES_DIR", "replay_histories")
)


def _build_replayer() -> Replayer:
    return Replayer(
        workflows=ALL_WORKFLOWS,
        workflow_runner=SandboxedWorkflowRunner(
            restrictions=SandboxRestrictions.default.with_passthrough_modules(
                "praw", "requests", "urllib3", "bot",
            )
        ),
    )


class ReplayDeterminismTest(unittest.IsolatedAsyncioTestCase):
    """Replay workflow histories from JSON files against current code."""

    async def test_replay_histories(self):
        """Replay all history files in the histories directory."""
        self.assertTrue(
            HISTORIES_DIR.exists(),
            f"Histories directory not found: {HISTORIES_DIR}",
        )

        history_files = sorted(HISTORIES_DIR.glob("*.json"))
        self.assertTrue(
            len(history_files) > 0,
            f"No JSON history files found in {HISTORIES_DIR}",
        )

        replayer = _build_replayer()

        for history_file in history_files:
            workflow_id = history_file.stem

            with self.subTest(workflow_id=workflow_id):
                history = WorkflowHistory.from_json(
                    workflow_id=workflow_id,
                    history=history_file.read_text(),
                )
                await replayer.replay_workflow(history)


if __name__ == "__main__":
    unittest.main()
