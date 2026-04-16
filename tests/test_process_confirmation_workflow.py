"""Workflow-level tests for ProcessConfirmationWorkflow.

Uses Temporal's time-skipping test server with mocked activities.
Activity stubs return realistic data captured from the pen_swap production server.
"""

import os
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Required by bot.config at import time
os.environ.setdefault("SUBREDDIT_NAME", "Pen_Swap")

from temporalio import activity, workflow  # noqa: E402
from temporalio.client import Client  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions  # noqa: E402

from bot.models import (  # noqa: E402
    CommentData,
    FlairIncrementRequest,
    FlairIncrementResult,
    ReplyToCommentInput,
    ValidationResult,
)

# ---------------------------------------------------------------------------
# Realistic fixtures from pen_swap production data
# ---------------------------------------------------------------------------

CONFIRMED_COMMENT = CommentData(
    id="oe2ihew",
    body="Confirmed thank you so much ",
    author_name="saturnsrings7",
    created_utc=1775223247.0,
    is_root=False,
    submission_id="1s94i6r",
)

VALID_VALIDATION = ValidationResult(
    valid=True,
    confirmer="saturnsrings7",
    is_mod_approval=False,
    parent_author="Difficult_Grass9608",
    parent_comment_id="oe0a7i3",
    reason=None,
    reply_to_comment_id="oe2ihew",
)

PARENT_FLAIR_RESULT = FlairIncrementResult(
    old_flair="Trades: 25",
    new_flair="Trades: 26",
)

CONFIRMER_FLAIR_RESULT = FlairIncrementResult(
    old_flair="Trades: 516",
    new_flair="Trades: 517",
)

REJECTED_COMMENT = CommentData(
    id="reject1",
    body="Confirmed",
    author_name="SomeUser",
    created_utc=1775223000.0,
    is_root=False,
    submission_id="1s94i6r",
)

ALREADY_CONFIRMED_VALIDATION = ValidationResult(
    valid=False,
    reason="already_confirmed",
    parent_author="OtherUser",
    parent_comment_id="parent1",
)

SKIPPED_COMMENT = CommentData(
    id="skip1",
    body="thanks",
    author_name="AnotherUser",
    created_utc=1775223000.0,
    is_root=False,
    submission_id="1s94i6r",
)

SKIPPED_VALIDATION = ValidationResult(valid=False)


# ---------------------------------------------------------------------------
# Activity stubs — registered with the test worker to intercept calls
# ---------------------------------------------------------------------------


@dataclass
class ActivityCalls:
    """Tracks which activities were called and with what arguments."""

    validate_confirmation: list[CommentData] | None = None
    request_flair_increment: list[FlairIncrementRequest] | None = None
    reply_to_comment: list[ReplyToCommentInput] | None = None
    mark_comment_saved: list[str] | None = None

    def __post_init__(self) -> None:
        self.validate_confirmation = []
        self.request_flair_increment = []
        self.reply_to_comment = []
        self.mark_comment_saved = []


def make_activity_stubs(
    calls: ActivityCalls,
    validation_result: ValidationResult = VALID_VALIDATION,
    parent_flair: FlairIncrementResult = PARENT_FLAIR_RESULT,
    confirmer_flair: FlairIncrementResult = CONFIRMER_FLAIR_RESULT,
) -> list:
    """Build activity stub functions that record calls and return canned data."""

    @activity.defn(name="validate_confirmation")
    async def stub_validate(comment_data: CommentData) -> ValidationResult:
        assert calls.validate_confirmation is not None
        calls.validate_confirmation.append(comment_data)
        return validation_result

    @activity.defn(name="request_flair_increment")
    async def stub_flair_increment(request: FlairIncrementRequest) -> FlairIncrementResult:
        assert calls.request_flair_increment is not None
        calls.request_flair_increment.append(request)
        if request.request_id.endswith(":parent"):
            return parent_flair
        return confirmer_flair

    @activity.defn(name="reply_to_comment")
    async def stub_reply(input: ReplyToCommentInput) -> str:
        assert calls.reply_to_comment is not None
        calls.reply_to_comment.append(input)
        return "reply_id"

    @activity.defn(name="mark_comment_saved")
    async def stub_save(comment_id: str) -> bool:
        assert calls.mark_comment_saved is not None
        calls.mark_comment_saved.append(comment_id)
        return True

    @activity.defn(name="send_pushover_notification")
    async def stub_notify(message: str) -> bool:
        return True

    return [stub_validate, stub_flair_increment, stub_reply, stub_save, stub_notify]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class ProcessConfirmationWorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def _run_workflow(
        self,
        comment: CommentData,
        calls: ActivityCalls,
        activities: list,
    ) -> dict:
        """Start a test environment, register the workflow + stubs, and run."""
        # Import inside test to avoid sandbox issues at module level
        from temporal.workflows.comment_processing import ProcessConfirmationWorkflow

        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue="test-queue",
                workflows=[ProcessConfirmationWorkflow],
                activities=activities,
                workflow_runner=SandboxedWorkflowRunner(
                    restrictions=SandboxRestrictions.default.with_passthrough_modules(
                        "praw", "requests", "urllib3", "bot", "temporal",
                    )
                ),
            ):
                result = await env.client.execute_workflow(
                    ProcessConfirmationWorkflow.run,
                    comment,
                    id=f"test-process-{comment.id}",
                    task_queue="test-queue",
                )
                return result

    async def test_confirmed_trade_happy_path(self):
        """Full confirmation flow based on real pen_swap data."""
        calls = ActivityCalls()
        activities = make_activity_stubs(calls)

        result = await self._run_workflow(CONFIRMED_COMMENT, calls, activities)

        # Workflow result matches expected shape
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["comment_id"], "oe2ihew")
        self.assertEqual(result["parent_author"], "Difficult_Grass9608")
        self.assertEqual(result["confirmer"], "saturnsrings7")
        self.assertEqual(result["parent_new_flair"], "Trades: 26")
        self.assertEqual(result["confirmer_new_flair"], "Trades: 517")

        # Validation was called with the input comment
        assert calls.validate_confirmation is not None
        self.assertEqual(len(calls.validate_confirmation), 1)
        self.assertEqual(calls.validate_confirmation[0].id, "oe2ihew")

        # Both flair increments were requested
        assert calls.request_flair_increment is not None
        self.assertEqual(len(calls.request_flair_increment), 2)
        usernames = {r.username for r in calls.request_flair_increment}
        self.assertEqual(usernames, {"Difficult_Grass9608", "saturnsrings7"})

        # Reply was posted
        assert calls.reply_to_comment is not None
        self.assertEqual(len(calls.reply_to_comment), 1)
        self.assertEqual(calls.reply_to_comment[0].template_name, "trade_confirmation")

        # Both parent comment and confirming comment were saved
        assert calls.mark_comment_saved is not None
        self.assertIn("oe0a7i3", calls.mark_comment_saved)
        self.assertIn("oe2ihew", calls.mark_comment_saved)

    async def test_rejected_already_confirmed(self):
        """Rejected comment gets a reply and is saved."""
        calls = ActivityCalls()
        activities = make_activity_stubs(
            calls, validation_result=ALREADY_CONFIRMED_VALIDATION
        )

        result = await self._run_workflow(REJECTED_COMMENT, calls, activities)

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "already_confirmed")
        self.assertEqual(result["comment_id"], "reject1")

        # Reply was posted with rejection template
        assert calls.reply_to_comment is not None
        self.assertEqual(len(calls.reply_to_comment), 1)
        self.assertEqual(
            calls.reply_to_comment[0].template_name, "already_confirmed"
        )

        # Comment was saved
        assert calls.mark_comment_saved is not None
        self.assertIn("reject1", calls.mark_comment_saved)

        # No flair increments
        assert calls.request_flair_increment is not None
        self.assertEqual(len(calls.request_flair_increment), 0)

    async def test_skipped_no_reason(self):
        """Invalid comment with no reason is silently skipped."""
        calls = ActivityCalls()
        activities = make_activity_stubs(
            calls, validation_result=SKIPPED_VALIDATION
        )

        result = await self._run_workflow(SKIPPED_COMMENT, calls, activities)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["comment_id"], "skip1")

        # No reply, no flair increments
        assert calls.reply_to_comment is not None
        self.assertEqual(len(calls.reply_to_comment), 0)
        assert calls.request_flair_increment is not None
        self.assertEqual(len(calls.request_flair_increment), 0)

        # Comment was still saved
        assert calls.mark_comment_saved is not None
        self.assertIn("skip1", calls.mark_comment_saved)

    async def test_flair_increment_request_ids_are_idempotent(self):
        """Verify the request IDs match the expected idempotency key pattern."""
        calls = ActivityCalls()
        activities = make_activity_stubs(calls)

        await self._run_workflow(CONFIRMED_COMMENT, calls, activities)

        assert calls.request_flair_increment is not None
        request_ids = {r.request_id for r in calls.request_flair_increment}
        # Keys are built from parent_comment_id:confirmer:(parent|confirmer)
        self.assertEqual(
            request_ids,
            {"oe0a7i3:saturnsrings7:parent", "oe0a7i3:saturnsrings7:confirmer"},
        )


if __name__ == "__main__":
    unittest.main()
