"""Temporal-based Reddit trade confirmation bot.

This package implements the trade confirmation bot using Temporal workflows
for reliable, durable execution.

Usage:
    # Start the worker
    python -m temporal.worker

    # Set up schedules (run once)
    python -m temporal.starter setup

    # Start polling
    python -m temporal.starter start-polling
"""

from .shared import TASK_QUEUE, SUBREDDIT_NAME

__all__ = ["TASK_QUEUE", "SUBREDDIT_NAME"]
