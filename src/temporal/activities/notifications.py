"""Notification activities for Temporal bot."""

import http.client
import os
import urllib.parse

from temporalio import activity


@activity.defn
def send_pushover_notification(message: str) -> bool:
    """Send notification via Pushover.

    Returns True if notification was sent successfully.
    Skips silently if Pushover is not configured.
    Raises exception on failure so Temporal can retry.
    """
    app_token = os.getenv("PUSHOVER_APP_TOKEN", "")
    user_token = os.getenv("PUSHOVER_USER_TOKEN", "")
    if not app_token or not user_token:
        activity.logger.debug("Pushover not configured, skipping notification")
        return True

    conn = http.client.HTTPSConnection("api.pushover.net:443")
    try:
        conn.request(
            "POST",
            "/1/messages.json",
            urllib.parse.urlencode(
                {
                    "token": app_token,
                    "user": user_token,
                    "message": message,
                }
            ),
            {"Content-type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        if response.status != 200:
            raise RuntimeError(
                f"Pushover notification failed with status {response.status}"
            )
        return True
    finally:
        conn.close()
