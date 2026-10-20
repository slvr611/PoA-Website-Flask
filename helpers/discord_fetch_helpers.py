"""Fetches the content of a single Discord message by URL, via the REST API
directly (no gateway/websocket client needed — see helpers/ai_review_helpers.py,
the only caller, which only ever needs to read specific messages by ID, never
listen for events).

Requires a Discord bot application invited to the relevant server with
`View Channel` + `Read Message History` on whatever channels players post RP
justification in, and the "Message Content" privileged intent enabled — none
of that can be done from code; see the plan doc for the one-time setup.
"""
import os
import re
from time import sleep

import requests

from app_core import DISCORD_LINK_PATTERN

# app_core.DISCORD_LINK_PATTERN matches the whole URL as one group; this adds
# capturing groups for guild/channel/message so a message can actually be
# fetched (format_discord_link, the only existing consumer of that pattern,
# never needed to pull the IDs back out — it just displays the matched URL).
_DISCORD_MESSAGE_URL_RE = re.compile(
    r'https://(?:ptb\.|canary\.)?discord\.com/channels/(\d+)/(\d+)/(\d+)'
)

DISCORD_API_BASE = "https://discord.com/api/v10"

# A single bounded retry on a 429 — this runs from a background worker with
# no user waiting on it, so there's no reason to retry harder than this;
# a message that's still rate-limited after one wait is picked up again on
# the worker's next poll (or via the manual "Request AI Review" re-trigger)
# rather than blocking this call indefinitely.
_MAX_RATE_LIMIT_RETRIES = 1
_MAX_RATE_LIMIT_WAIT_SECONDS = 10


def _parse_discord_message_url(url):
    match = _DISCORD_MESSAGE_URL_RE.search(url or "")
    if not match:
        return None
    guild_id, channel_id, message_id = match.groups()
    return {"guild_id": guild_id, "channel_id": channel_id, "message_id": message_id}


def _parse_retry_after_seconds(response):
    """Discord's rate-limit responses carry retry_after (seconds, float) in
    the JSON body; the standard Retry-After header is a fallback for
    anything that doesn't follow that shape."""
    try:
        body = response.json()
        if isinstance(body, dict) and "retry_after" in body:
            return float(body["retry_after"])
    except ValueError:
        pass
    try:
        return float(response.headers.get("Retry-After", 1))
    except (TypeError, ValueError):
        return 1.0


def fetch_discord_message(url, bot_token=None, timeout=10):
    """Fetch one Discord message's content by its channels/{guild}/{channel}/{message}
    URL. Always returns a dict, never raises — every failure mode (a
    malformed URL, a missing bot token, a deleted message, a channel the bot
    can't see, a network error, an un-parseable response) is a "can't judge
    this" signal for the caller, not an exception that should abort a whole
    review batch.

    Returns `{"url", "ok": True, "content", "author", "timestamp", "attachments"}`
    on success, or `{"url", "ok": False, "error": "<reason>"}` on failure —
    `error` is one of: invalid_url, missing_token, not_found, forbidden,
    rate_limited, request_failed, invalid_response.
    """
    parsed = _parse_discord_message_url(url)
    if parsed is None:
        return {"url": url, "ok": False, "error": "invalid_url"}

    token = bot_token or os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        return {"url": url, "ok": False, "error": "missing_token"}

    endpoint = f"{DISCORD_API_BASE}/channels/{parsed['channel_id']}/messages/{parsed['message_id']}"
    headers = {"Authorization": f"Bot {token}"}

    attempt = 0
    while True:
        try:
            response = requests.get(endpoint, headers=headers, timeout=timeout)
        except requests.RequestException:
            return {"url": url, "ok": False, "error": "request_failed"}

        if response.status_code == 200:
            break
        if response.status_code == 429 and attempt < _MAX_RATE_LIMIT_RETRIES:
            wait_seconds = min(_parse_retry_after_seconds(response), _MAX_RATE_LIMIT_WAIT_SECONDS)
            sleep(wait_seconds)
            attempt += 1
            continue
        if response.status_code == 429:
            return {"url": url, "ok": False, "error": "rate_limited"}
        if response.status_code in (403, 401):
            return {"url": url, "ok": False, "error": "forbidden"}
        if response.status_code == 404:
            return {"url": url, "ok": False, "error": "not_found"}
        return {"url": url, "ok": False, "error": "request_failed"}

    try:
        data = response.json()
    except ValueError:
        return {"url": url, "ok": False, "error": "invalid_response"}

    return {
        "url": url,
        "ok": True,
        "content": data.get("content", ""),
        "author": (data.get("author") or {}).get("username"),
        "timestamp": data.get("timestamp"),
        "attachments": [a["url"] for a in data.get("attachments", []) if a.get("url")],
    }


def fetch_discord_messages(urls, bot_token=None, timeout=10):
    """fetch_discord_message for every URL in `urls`, in order. One failed
    fetch never drops the others — each result dict says for itself whether
    it succeeded (see fetch_discord_message)."""
    return [fetch_discord_message(url, bot_token=bot_token, timeout=timeout) for url in urls]
