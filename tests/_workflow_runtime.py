"""Helpers to unit test workflow methods without a Temporal test server."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class FakeLogger:
    """Collect log entries for assertions in tests."""

    infos: list[tuple] = field(default_factory=list)
    warnings: list[tuple] = field(default_factory=list)
    errors: list[tuple] = field(default_factory=list)

    def info(self, *args, **kwargs):
        self.infos.append(args)

    def warning(self, *args, **kwargs):
        self.warnings.append(args)

    def error(self, *args, **kwargs):
        self.errors.append(args)


class FakeWorkflowRuntime:
    """Simple runtime shim for workflow module-level APIs.

    It emulates the subset of `temporalio.workflow` used in unit tests.
    """

    def __init__(self, activity_handlers: dict[str, Any] | None = None):
        self.activity_handlers = activity_handlers or {}
        self.activity_calls: list[tuple[str, list[Any]]] = []
        self.child_calls: list[tuple[str, list[Any]]] = []
        self.sleeps: list[Any] = []
        self.logger = FakeLogger()

    async def execute_activity(self, activity_fn, args=None, **kwargs):
        activity_name = activity_fn.__name__
        call_args = list(args or [])
        self.activity_calls.append((activity_name, call_args))

        if activity_name not in self.activity_handlers:
            raise AssertionError(f"No fake handler for activity '{activity_name}'")

        handler = self.activity_handlers[activity_name]
        if isinstance(handler, BaseException):
            raise handler

        if callable(handler):
            result = handler(*call_args)
            if inspect.isawaitable(result):
                return await result
            return result

        return handler

    async def execute_child_workflow(self, workflow_run_fn, args=None, **kwargs):
        workflow_name = getattr(workflow_run_fn, "__qualname__", str(workflow_run_fn))
        self.child_calls.append((workflow_name, list(args or [])))
        return None

    async def sleep(self, duration):
        self.sleeps.append(duration)

    def continue_as_new(self, *args, **kwargs):
        raise AssertionError("continue_as_new should not be called in these tests")
