"""Discord webhook notification sender."""

import logging
import json
import urllib.request
import urllib.error

logger = logging.getLogger("offloader.discord")

# Discord blocks the default Python-urllib User-Agent with 403 Forbidden.
# A proper User-Agent header is required for all webhook requests.
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "FootageOffloader/1.0 (Raspberry Pi; webhook bot)",
}


def _post_webhook(webhook_url: str, payload: dict):
    """Build and execute a Discord webhook POST request."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers=HEADERS,
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=15)


def send_discord_notification(webhook_url: str, message: str, username: str = "Footage Offloader"):
    """Send a message to a Discord webhook."""
    if not webhook_url:
        return

    payload = {
        "username": username,
        "content": message,
    }

    try:
        resp = _post_webhook(webhook_url, payload)
        resp.close()
        logger.info("Discord notification sent")
    except urllib.error.HTTPError as e:
        logger.error(f"Discord HTTP error: {e.code} {e.reason}")
    except urllib.error.URLError as e:
        logger.error(f"Discord URL error: {e.reason}")
    except Exception as e:
        logger.error(f"Discord notification failed: {e}")


def test_discord_webhook(webhook_url: str) -> dict:
    """Test if a Discord webhook URL is valid by sending a test message."""
    if not webhook_url:
        return {"success": False, "error": "No webhook URL configured"}

    if "discord.com/api/webhooks/" not in webhook_url:
        return {"success": False, "error": "Invalid webhook URL — must be a Discord webhook URL"}

    payload = {
        "username": "Footage Offloader",
        "content": "🔔 **Footage Offloader** — Test notification\nWebhook is working correctly!",
    }

    try:
        resp = _post_webhook(webhook_url, payload)
        status = resp.status
        resp.close()
        if status in (200, 204):
            return {"success": True, "message": "Test notification sent"}
        return {"success": False, "error": f"Discord returned status {status}"}
    except urllib.error.HTTPError as e:
        return {"success": False, "error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"success": False, "error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
