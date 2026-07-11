"""
Telegram Bot API notification helper.

No third-party library needed — the Telegram Bot API is a simple HTTPS endpoint.
"""
import logging

import requests

log = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/{method}"


def send_message(bot_token: str, chat_id: str, text: str) -> bool:
    """Send a text message via a Telegram bot. Returns True on success."""
    url = _API_BASE.format(token=bot_token, method="sendMessage")
    try:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log.warning("Telegram send failed: %s", e)
        return False
