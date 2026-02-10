"""Test environment helpers."""

import os


def ensure_test_env() -> None:
    """Set minimal environment variables required by temporal.shared."""
    defaults = {
        "SUBREDDIT_NAME": "testsubreddit",
        "REDDIT_CLIENT_ID": "test-client-id",
        "REDDIT_CLIENT_SECRET": "test-client-secret",
        "REDDIT_USER_AGENT": "test-user-agent",
        "REDDIT_USERNAME": "test-bot",
        "REDDIT_PASSWORD": "test-password",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
