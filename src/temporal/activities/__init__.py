"""Temporal activities for Reddit trade confirmation bot."""

from .comments import (
    fetch_new_comments,
    mark_comment_saved,
    post_confirmation_reply,
    reply_to_comment,
    validate_confirmation,
)
from .flair import get_user_flair, increment_user_flair_atomic, set_user_flair
from .notifications import send_pushover_notification
from .temporal_bridge import request_user_flair_increment
from .submissions import (
    create_monthly_post,
    lock_previous_submissions,
    unsticky_previous_post,
)

__all__ = [
    # Comments
    "fetch_new_comments",
    "validate_confirmation",
    "mark_comment_saved",
    "reply_to_comment",
    "post_confirmation_reply",
    # Flair
    "get_user_flair",
    "set_user_flair",
    "increment_user_flair_atomic",
    "request_user_flair_increment",
    # Submissions
    "create_monthly_post",
    "unsticky_previous_post",
    "lock_previous_submissions",
    # Notifications
    "send_pushover_notification",
]
