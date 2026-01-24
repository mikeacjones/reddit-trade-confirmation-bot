"""Notification activities for Temporal bot."""

import http.client
import urllib.parse
from temporalio import activity

from ..shared import SECRETS, LOGGER


@activity.defn
async def send_pushover_notification(message: str) -> bool:
    """Send notification via Pushover.

    Returns True if notification was sent successfully (or skipped due to no config).
    """
    if not SECRETS.get("PUSHOVER_APP_TOKEN") or not SECRETS.get("PUSHOVER_USER_TOKEN"):
        LOGGER.debug("Pushover not configured, skipping notification")
        return True

    try:
        conn = http.client.HTTPSConnection("api.pushover.net:443")
        conn.request(
            "POST",
            "/1/messages.json",
            urllib.parse.urlencode({
                "token": SECRETS["PUSHOVER_APP_TOKEN"],
                "user": SECRETS["PUSHOVER_USER_TOKEN"],
                "message": message,
            }),
            {"Content-type": "application/x-www-form-urlencoded"},
        )
        response = conn.getresponse()
        if response.status != 200:
            LOGGER.warning("Pushover notification failed: %s", response.status)
            return False
        return True
    except Exception as e:
        LOGGER.error("Failed to send Pushover notification: %s", e)
        return False
