"""Pushover notification service wrapper."""
import http.client
import urllib


class Pushover:
    """Simple Pushover notification client."""
    
    def __init__(self, app_token: str, user_token: str):
        self.app_token = app_token
        self.user_token = user_token
    
    def send_message(self, message: str, title: str = "Trade Bot"):
        """Sends a push notification via Pushover."""
        conn = http.client.HTTPSConnection("api.pushover.net:443")
        conn.request(
            "POST",
            "/1/messages.json",
            urllib.parse.urlencode({
                "token": self.app_token,
                "user": self.user_token,
                "message": message,
                "title": title,
            }),
            {"Content-type": "application/x-www-form-urlencoded"}
        )
        conn.getresponse()