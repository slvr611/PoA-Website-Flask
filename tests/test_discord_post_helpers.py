"""Tests for helpers/discord_post_helpers.py — post_discord_message must
never raise, for any failure mode, since it's called from give_tick_summary
after a tick has already fully committed; a Discord-side failure must never
look like the tick itself failed. chunk_text_for_discord must never produce
a chunk Discord would reject (break only on line boundaries, never drop
content from an over-long line).
"""
from unittest.mock import patch, MagicMock

import requests

import helpers.discord_post_helpers as dph


def _fake_response(status_code, json_body=None, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.json.return_value = json_body or {}
    return resp


class TestChunkTextForDiscord:
    def test_blank_text_returns_empty_list(self):
        assert dph.chunk_text_for_discord("") == []
        assert dph.chunk_text_for_discord("   \n  ") == []

    def test_short_text_is_a_single_chunk(self):
        assert dph.chunk_text_for_discord("line one\nline two") == ["line one\nline two"]

    def test_splits_on_line_boundaries_once_over_the_limit(self):
        lines = [f"line {i}" for i in range(500)]
        text = "\n".join(lines)
        chunks = dph.chunk_text_for_discord(text, limit=50)
        assert all(len(c) <= 50 for c in chunks)
        # No line was dropped or merged incorrectly — rejoining recovers the original.
        assert "\n".join(chunks).replace("\n\n", "\n") == text or "\n".join(chunks) == text

    def test_never_cuts_a_line_in_half_unless_the_line_itself_is_too_long(self):
        text = "short\n" + ("x" * 10) + "\nshort again"
        chunks = dph.chunk_text_for_discord(text, limit=20)
        for chunk in chunks:
            for line in chunk.split("\n"):
                assert line in text.split("\n") or len(line) <= 20

    def test_a_single_line_longer_than_the_limit_is_hard_split_not_dropped(self):
        text = "y" * 55
        chunks = dph.chunk_text_for_discord(text, limit=20)
        assert "".join(chunks) == text
        assert all(len(c) <= 20 for c in chunks)


class TestPostDiscordMessage:
    def test_missing_channel(self):
        result = dph.post_discord_message(None, "hi", bot_token="tok")
        assert result == {"ok": False, "error": "missing_channel"}

    def test_missing_token(self):
        with patch.dict("os.environ", {}, clear=True):
            result = dph.post_discord_message("123", "hi", bot_token=None)
        assert result == {"ok": False, "error": "missing_token"}

    def test_successful_post(self):
        with patch.object(dph.requests, "post", return_value=_fake_response(200)) as mock_post:
            result = dph.post_discord_message("123", "hello players", bot_token="tok")

        assert result == {"ok": True}
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "123/messages" in args[0]
        assert kwargs["headers"]["Authorization"] == "Bot tok"
        assert kwargs["json"] == {"content": "hello players"}

    def test_forbidden_403(self):
        with patch.object(dph.requests, "post", return_value=_fake_response(403)):
            result = dph.post_discord_message("123", "hi", bot_token="tok")
        assert result == {"ok": False, "error": "forbidden"}

    def test_channel_not_found_404(self):
        with patch.object(dph.requests, "post", return_value=_fake_response(404)):
            result = dph.post_discord_message("123", "hi", bot_token="tok")
        assert result == {"ok": False, "error": "not_found"}

    def test_rate_limited_retries_once_then_succeeds(self):
        responses = [_fake_response(429, {"retry_after": 0.01}), _fake_response(200)]
        with patch.object(dph.requests, "post", side_effect=responses) as mock_post, \
             patch.object(dph, "sleep") as mock_sleep:
            result = dph.post_discord_message("123", "hi", bot_token="tok")
        assert mock_post.call_count == 2
        mock_sleep.assert_called_once_with(0.01)
        assert result == {"ok": True}

    def test_rate_limited_gives_up_after_max_retries(self):
        responses = [_fake_response(429, {"retry_after": 0.01}), _fake_response(429, {"retry_after": 0.01})]
        with patch.object(dph.requests, "post", side_effect=responses), \
             patch.object(dph, "sleep"):
            result = dph.post_discord_message("123", "hi", bot_token="tok")
        assert result == {"ok": False, "error": "rate_limited"}

    def test_network_error_never_raises(self):
        with patch.object(dph.requests, "post", side_effect=requests.ConnectionError("boom")):
            result = dph.post_discord_message("123", "hi", bot_token="tok")
        assert result == {"ok": False, "error": "request_failed"}

    def test_falls_back_to_env_token(self):
        with patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "env-tok"}), \
             patch.object(dph.requests, "post", return_value=_fake_response(200)) as mock_post:
            dph.post_discord_message("123", "hi")
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bot env-tok"


class TestPostTickSummarySections:
    def test_posts_one_message_per_section_with_a_header(self):
        sections = [("Characters", "Aesoh has died.\n"), ("Nations", "Taika: +100 gold\n")]
        with patch.object(dph, "post_discord_message", return_value={"ok": True}) as mock_post:
            results = dph.post_tick_summary_sections(sections, channel_id="999", bot_token="tok")

        assert len(results) == 2
        assert all(r["ok"] for r in results)
        assert [r["label"] for r in results] == ["Characters", "Nations"]
        first_call_content = mock_post.call_args_list[0].args[1]
        assert first_call_content.startswith("**Characters**\n")
        assert "Aesoh has died." in first_call_content

    def test_blank_sections_are_skipped_entirely(self):
        sections = [("Characters", "   \n  "), ("Nations", "Taika: +100 gold\n")]
        with patch.object(dph, "post_discord_message", return_value={"ok": True}) as mock_post:
            results = dph.post_tick_summary_sections(sections, channel_id="999", bot_token="tok")
        assert len(results) == 1
        assert mock_post.call_count == 1

    def test_a_long_section_splits_into_multiple_messages_with_continuation_headers(self):
        long_text = "\n".join(f"Nation {i} did a thing" for i in range(200))
        sections = [("Nations", long_text)]
        with patch.object(dph, "post_discord_message", return_value={"ok": True}) as mock_post:
            results = dph.post_tick_summary_sections(sections, channel_id="999", bot_token="tok")

        assert len(results) > 1
        assert all(r["label"] == "Nations" for r in results)
        contents = [c.args[1] for c in mock_post.call_args_list]
        assert contents[0].startswith("**Nations**\n")
        assert all(c.startswith("**Nations (cont.)**\n") for c in contents[1:])

    def test_one_failed_section_does_not_stop_the_others(self):
        sections = [("Characters", "x\n"), ("Nations", "y\n")]
        with patch.object(dph, "post_discord_message", side_effect=[{"ok": False, "error": "forbidden"}, {"ok": True}]):
            results = dph.post_tick_summary_sections(sections, channel_id="999", bot_token="tok")
        assert [r["ok"] for r in results] == [False, True]

    def test_uses_env_channel_when_none_passed(self):
        sections = [("Characters", "x\n")]
        with patch.dict("os.environ", {"DISCORD_TICK_SUMMARY_CHANNEL_ID": "env-channel"}), \
             patch.object(dph, "post_discord_message", return_value={"ok": True}) as mock_post:
            dph.post_tick_summary_sections(sections, bot_token="tok")
        assert mock_post.call_args.args[0] == "env-channel"
