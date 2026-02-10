"""Workflow logic tests using a lightweight runtime shim."""

import unittest
from unittest.mock import patch

from tests._env import ensure_test_env
from tests._workflow_runtime import FakeWorkflowRuntime

ensure_test_env()

from temporal.workflows import comment_processing


class ProcessConfirmationWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_thread_replies_and_locks_comment(self):
        runtime = FakeWorkflowRuntime(
            {
                "validate_confirmation": {
                    "valid": False,
                    "reason": "old_confirmation_thread",
                },
                "reply_to_comment": "reply-id",
                "lock_comment": True,
            }
        )

        with patch.object(comment_processing, "workflow", runtime):
            wf = comment_processing.ProcessConfirmationWorkflow()
            result = await wf.run({"id": "abc123", "author_name": "alice"})

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "old_confirmation_thread")
        self.assertEqual(
            [name for name, _ in runtime.activity_calls],
            ["validate_confirmation", "reply_to_comment", "lock_comment"],
        )

    async def test_non_control_failure_marks_manual_review_and_notifies(self):
        runtime = FakeWorkflowRuntime(
            {
                "validate_confirmation": RuntimeError("validation blew up"),
                "send_pushover_notification": True,
            }
        )

        with patch.object(comment_processing, "workflow", runtime):
            wf = comment_processing.ProcessConfirmationWorkflow()
            result = await wf.run({"id": "def456", "author_name": "bob"})

        self.assertEqual(result["status"], "manual_review_required")
        self.assertEqual(result["error_type"], "RuntimeError")
        self.assertIn("validation blew up", result["error"])
        self.assertEqual(
            [name for name, _ in runtime.activity_calls],
            ["validate_confirmation", "send_pushover_notification"],
        )

    async def test_cancellation_style_error_propagates(self):
        class FakeCancellationError(Exception):
            pass

        runtime = FakeWorkflowRuntime(
            {
                "validate_confirmation": FakeCancellationError("cancel requested"),
            }
        )

        with patch.object(comment_processing, "workflow", runtime):
            wf = comment_processing.ProcessConfirmationWorkflow()
            with self.assertRaises(FakeCancellationError):
                await wf.run({"id": "ghi789", "author_name": "carol"})

        self.assertEqual([name for name, _ in runtime.activity_calls], ["validate_confirmation"])


class CommentPollingWorkflowReloadSignalTests(unittest.IsolatedAsyncioTestCase):
    async def test_reload_signal_runs_activity_and_clears_flag(self):
        runtime = FakeWorkflowRuntime(
            {
                "reload_flair_metadata_cache": {"templates": 3, "moderators": 2},
            }
        )

        with patch.object(comment_processing, "workflow", runtime):
            wf = comment_processing.CommentPollingWorkflow()
            wf.reload_flair_metadata()
            self.assertTrue(wf._reload_flair_metadata_requested)

            await wf._handle_reload_flair_metadata()

            self.assertFalse(wf._reload_flair_metadata_requested)
            self.assertEqual(
                [name for name, _ in runtime.activity_calls],
                ["reload_flair_metadata_cache"],
            )

    async def test_reload_failure_keeps_flag_set_for_retry(self):
        runtime = FakeWorkflowRuntime(
            {
                "reload_flair_metadata_cache": RuntimeError("temporary failure"),
            }
        )

        with patch.object(comment_processing, "workflow", runtime):
            wf = comment_processing.CommentPollingWorkflow()
            wf.reload_flair_metadata()

            await wf._handle_reload_flair_metadata()

            self.assertTrue(wf._reload_flair_metadata_requested)
            self.assertEqual(
                [name for name, _ in runtime.activity_calls],
                ["reload_flair_metadata_cache"],
            )


if __name__ == "__main__":
    unittest.main()
