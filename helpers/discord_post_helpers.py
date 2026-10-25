"""Posts messages to a Discord channel via the REST API directly (the same
bot application/token discord_fetch_helpers.py already uses to read
messages) — currently only for the session tick's player-facing summary,
posted to the "bot spam" channel section-by-section. See
helpers/tick_helpers.py's give_tick_summary, the only caller.

Requires the bot to additionally have `Send Messages` in whatever channel
DISCORD_TICK_SUMMARY_CHANNEL_ID points at — none of that can be set up
from code.
"""
import os
from time import sleep

import requests

from helpers.discord_fetch_helpers import DISCORD_API_BASE

# Discord hard-caps a single message at 2000 characters. Left with a small
# margin rather than cutting it exactly at 2000 — chunking below always
# breaks on a line boundary, and the last line before a cut could otherwise
# land exactly on the limit and get rejected by a stray trailing character.
_DISCORD_MESSAGE_CHAR_LIMIT = 1900

_MAX_RATE_LIMIT_RETRIES = 1
_MAX_RATE_LIMIT_WAIT_SECONDS = 10


def _parse_retry_after_seconds(response):
    """Mirrors discord_fetch_helpers.py's identical helper — kept separate
    rather than imported since the two modules are meant to stay
    independently readable (one fetches, one posts), not coupled through a
    shared internal."""
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


def chunk_text_for_discord(text, limit=_DISCORD_MESSAGE_CHAR_LIMIT):
    """Split `text` into a list of chunks, each at most `limit` characters,
    breaking only on line boundaries (never mid-line) so a chunk boundary
    never cuts a sentence in half. A single line longer than `limit` is
    hard-split as a last resort (never silently dropped).

    Returns [] for blank/whitespace-only text.
    """
    text = (text or "").strip("\n")
    if not text.strip():
        return []

    chunks = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        chunks.append(current)
    return chunks


def post_discord_message(channel_id, content, bot_token=None, timeout=10):
    """Post one message to a Discord channel. Always returns a dict, never
    raises — same "can't post this, don't abort the caller" philosophy as
    discord_fetch_helpers.fetch_discord_message.

    Returns {"ok": True} on success, or {"ok": False, "error": "<reason>"}
    — error is one of: missing_channel, missing_token, forbidden, not_found,
    rate_limited, request_failed, invalid_response.

    Does NOT chunk long content itself — see chunk_text_for_discord for
    splitting a long section into multiple calls.
    """
    if not channel_id:
        return {"ok": False, "error": "missing_channel"}
    token = bot_token or os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        return {"ok": False, "error": "missing_token"}

    endpoint = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}"}

    attempt = 0
    while True:
        try:
            response = requests.post(endpoint, headers=headers, json={"content": content}, timeout=timeout)
        except requests.RequestException:
            return {"ok": False, "error": "request_failed"}

        if response.status_code in (200, 201):
            break
        if response.status_code == 429 and attempt < _MAX_RATE_LIMIT_RETRIES:
            wait_seconds = min(_parse_retry_after_seconds(response), _MAX_RATE_LIMIT_WAIT_SECONDS)
            sleep(wait_seconds)
            attempt += 1
            continue
        if response.status_code == 429:
            return {"ok": False, "error": "rate_limited"}
        if response.status_code in (403, 401):
            return {"ok": False, "error": "forbidden"}
        if response.status_code == 404:
            return {"ok": False, "error": "not_found"}
        return {"ok": False, "error": "request_failed"}

    return {"ok": True}


def post_tick_summary_sections(sections, channel_id=None, bot_token=None):
    """Post one or more Discord messages per (label, text) section in
    `sections` — each section gets a bold header line, then its text
    chunked to fit Discord's message limit (see chunk_text_for_discord).
    Blank sections are skipped entirely (no empty header posted).

    One section/chunk failing to post never stops the rest — returns a
    list of per-chunk result dicts (each from post_discord_message, plus
    "label") so the caller can log/report what did and didn't make it
    through.
    """
    channel_id = channel_id or os.getenv("DISCORD_TICK_SUMMARY_CHANNEL_ID")
    results = []
    for label, text in sections:
        chunks = chunk_text_for_discord(text)
        if not chunks:
            continue
        for i, chunk in enumerate(chunks):
            header = f"**{label}**" if i == 0 else f"**{label} (cont.)**"
            content = f"{header}\n{chunk}"
            result = post_discord_message(channel_id, content, bot_token=bot_token)
            # A new dict, never a mutation of what post_discord_message
            # returned — that return value could legitimately be a shared/
            # cached object for some future implementation, and mutating it
            # here would silently corrupt every other result alias to it.
            results.append({**result, "label": label})
    return results
