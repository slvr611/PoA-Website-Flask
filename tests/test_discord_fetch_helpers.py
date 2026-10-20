"""Tests for helpers/discord_fetch_helpers.py — fetch_discord_message must
never raise, for any failure mode, since it's called from a background
worker reviewing a whole batch of changes; one bad link must never abort
the others.
"""
from unittest.mock import patch, MagicMock

import pytest
import requests

import helpers.discord_fetch_helpers as dfh


_VALID_URL = "https://discord.com/channels/111/222/333"


def _fake_response(status_code, json_body=None, headers=None, raise_on_json=False):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    if raise_on_json:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = json_body or {}
    return resp


class TestParseDiscordMessageUrl:
    def test_valid_url(self):
        parsed = dfh._parse_discord_message_url(_VALID_URL)
        assert parsed == {"guild_id": "111", "channel_id": "222", "message_id": "333"}

    def test_ptb_and_canary_subdomains(self):
        assert dfh._parse_discord_message_url("https://ptb.discord.com/channels/1/2/3") is not None
        assert dfh._parse_discord_message_url("https://canary.discord.com/channels/1/2/3") is not None

    def test_invalid_url_returns_none(self):
        assert dfh._parse_discord_message_url("https://example.com/not-discord") is None

    def test_none_input_returns_none(self):
        assert dfh._parse_discord_message_url(None) is None


class TestFetchDiscordMessage:
    def test_invalid_url(self):
        result = dfh.fetch_discord_message("not a discord link", bot_token="x")
        assert result == {"url": "not a discord link", "ok": False, "error": "invalid_url"}

    def test_missing_token(self):
        with patch.dict("os.environ", {}, clear=True):
            result = dfh.fetch_discord_message(_VALID_URL, bot_token=None)
        assert result == {"url": _VALID_URL, "ok": False, "error": "missing_token"}

    def test_successful_fetch(self):
        body = {
            "content": "This explains the wood stockpile change.",
            "author": {"username": "someplayer"},
            "timestamp": "2026-01-01T00:00:00+00:00",
            "attachments": [{"url": "https://cdn.discordapp.com/x.png"}, {"no_url": True}],
        }
        with patch.object(dfh.requests, "get", return_value=_fake_response(200, body)) as mock_get:
            result = dfh.fetch_discord_message(_VALID_URL, bot_token="tok123")

        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert "222/messages/333" in args[0]
        assert kwargs["headers"]["Authorization"] == "Bot tok123"
        assert result == {
            "url": _VALID_URL, "ok": True,
            "content": "This explains the wood stockpile change.",
            "author": "someplayer",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "attachments": ["https://cdn.discordapp.com/x.png"],
        }

    def test_deleted_message_404(self):
        with patch.object(dfh.requests, "get", return_value=_fake_response(404)):
            result = dfh.fetch_discord_message(_VALID_URL, bot_token="tok")
        assert result == {"url": _VALID_URL, "ok": False, "error": "not_found"}

    def test_no_channel_access_403(self):
        with patch.object(dfh.requests, "get", return_value=_fake_response(403)):
            result = dfh.fetch_discord_message(_VALID_URL, bot_token="tok")
        assert result == {"url": _VALID_URL, "ok": False, "error": "forbidden"}

    def test_unauthorized_401(self):
        with patch.object(dfh.requests, "get", return_value=_fake_response(401)):
            result = dfh.fetch_discord_message(_VALID_URL, bot_token="bad-tok")
        assert result == {"url": _VALID_URL, "ok": False, "error": "forbidden"}

    def test_rate_limited_retries_once_then_succeeds(self):
        responses = [
            _fake_response(429, {"retry_after": 0.01}),
            _fake_response(200, {"content": "ok", "author": {"username": "a"}, "timestamp": "t", "attachments": []}),
        ]
        with patch.object(dfh.requests, "get", side_effect=responses) as mock_get, \
             patch.object(dfh, "sleep") as mock_sleep:
            result = dfh.fetch_discord_message(_VALID_URL, bot_token="tok")

        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(0.01)
        assert result["ok"] is True

    def test_rate_limited_gives_up_after_max_retries(self):
        responses = [_fake_response(429, {"retry_after": 0.01}), _fake_response(429, {"retry_after": 0.01})]
        with patch.object(dfh.requests, "get", side_effect=responses), \
             patch.object(dfh, "sleep"):
            result = dfh.fetch_discord_message(_VALID_URL, bot_token="tok")
        assert result == {"url": _VALID_URL, "ok": False, "error": "rate_limited"}

    def test_network_error_never_raises(self):
        with patch.object(dfh.requests, "get", side_effect=requests.ConnectionError("boom")):
            result = dfh.fetch_discord_message(_VALID_URL, bot_token="tok")
        assert result == {"url": _VALID_URL, "ok": False, "error": "request_failed"}

    def test_unparseable_json_response(self):
        with patch.object(dfh.requests, "get", return_value=_fake_response(200, raise_on_json=True)):
            result = dfh.fetch_discord_message(_VALID_URL, bot_token="tok")
        assert result == {"url": _VALID_URL, "ok": False, "error": "invalid_response"}

    def test_unexpected_status_code(self):
        with patch.object(dfh.requests, "get", return_value=_fake_response(500)):
            result = dfh.fetch_discord_message(_VALID_URL, bot_token="tok")
        assert result == {"url": _VALID_URL, "ok": False, "error": "request_failed"}

    def test_bot_token_param_overrides_env(self):
        with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "env-token"}), \
             patch.object(dfh.requests, "get", return_value=_fake_response(200, {
                 "content": "", "author": {}, "timestamp": "", "attachments": [],
             })) as mock_get:
            dfh.fetch_discord_message(_VALID_URL, bot_token="explicit-token")
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bot explicit-token"

    def test_falls_back_to_env_token(self):
        with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "env-token"}), \
             patch.object(dfh.requests, "get", return_value=_fake_response(200, {
                 "content": "", "author": {}, "timestamp": "", "attachments": [],
             })) as mock_get:
            dfh.fetch_discord_message(_VALID_URL)
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bot env-token"


class TestFetchDiscordMessages:
    def test_fetches_each_url_independently(self):
        def fake_fetch(url, bot_token=None, timeout=10):
            return {"url": url, "ok": url != "bad"}

        with patch.object(dfh, "fetch_discord_message", side_effect=fake_fetch):
            results = dfh.fetch_discord_messages(["good1", "bad", "good2"], bot_token="tok")

        assert [r["ok"] for r in results] == [True, False, True]

    def test_empty_list(self):
        assert dfh.fetch_discord_messages([]) == []
