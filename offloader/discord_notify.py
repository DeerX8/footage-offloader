"""Discord webhook notification sender."""

import logging
import json
import urllib.request
import urllib.error

logger = logging.getLogger("offloader.discord")


def send_discord_notification(webhook_url: str, message: str, username: str = "Footage Offloader"):
    """Send a message to a Discord webhook.
    
    Uses urllib to avoid requiring the requests library.
    """
    if not webhook_url:
        return

    payload = {
        "username": username,
        "content": message,
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status in (200, 204):
                logger.info("Discord notification sent")
            else:
                logger.warning(f"Discord returned status {response.status}")
    except urllib.error.HTTPError as e:
        logger.error(f"Discord HTTP error: {e.code} {e.reason}")
    except urllib.error.URLError as e:
        logger.error(f"Discord URL error: {e.reason}")
    except Exception as e:
        logger.error(f"Discord notification failed: {e}")


def test_discord_webhook(webhook_url: str) -> dict:
    """Test if a Discord webhook URL is valid by sending a test message."""
    if not webhook_url:
        return {"success": False, "error": "No webhook URL provided"}

    try:
        send_discord_notification(
            webhook_url,
            "🔔 **Footage Offloader** - Test notification\nWebhook is working correctly!"
        )
        return {"success": True, "message": "Test notification sent"}
    except Exception as e:
        return {"success": False, "error": str(e)}
