"""Temporal activities for Reddit trade confirmation bot."""

from .comments import (
    poll_new_comments,
    mark_comment_saved,
    reply_to_comment,
    validate_confirmation,
)
from .flair import FlairCoordinatorActivity, get_user_flair, set_user_flair
from .notifications import send_pushover_notification
from .submissions import (
    create_monthly_post,
    fetch_active_submission_ids,
    lock_submission,
    sticky_submission,
    unsticky_submission,
)

__all__ = [
    # Comments
    "poll_new_comments",
    "validate_confirmation",
    "mark_comment_saved",
    "reply_to_comment",
    # Flair
    "get_user_flair",
    "set_user_flair",
    "FlairCoordinatorActivity",
    # Submissions
    "create_monthly_post",
    "fetch_active_submission_ids",
    "sticky_submission",
    "unsticky_submission",
    "lock_submission",
    # Notifications
    "send_pushover_notification",
]
