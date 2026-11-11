"""Tests for give_tick_summary (helpers/tick_helpers.py) persisting
player_tick_summary_sections as a tick_summary_sections_<timestamp>.json
sidecar alongside the two .txt files it already saves.

Added 2026-10-26: routes/admin_tool_routes.py's post_tick_summary_to_discord
("Post to Discord" button on an already-generated summary) needs this
sidecar to reproduce the automatic path's per-tick-function sections and
"<@discord_id> " mentions -- the flat player_tick_summary text saved
before this change never carried either (see test_tick_summary_sections.py).
Without the sidecar, a manual repost could only dump one undifferentiated,
mention-less chunk of text, which is exactly the gap this fixes.
"""
import json
import os
from unittest.mock import patch

import helpers.tick_helpers as th


class TestGiveTickSummarySectionsSidecar:
    def test_sections_are_saved_as_a_matching_json_sidecar(self, mock_mongo, tmp_path):
        sections = [("Nation Stability Tick", "<@111> Taika: stability rose.\n")]

        # conftest.py neutralizes dotenv.load_dotenv before app_core is ever
        # imported (its own safety measure against tests hitting the real
        # S3 bucket), so S3_BUCKET_NAME is normally unset here -- set it
        # explicitly to actually exercise the upload branch below.
        with patch.dict(os.environ, {
                 "S3_BUCKET_NAME": "test-bucket",
                 "AWS_ACCESS_KEY_ID": "test-key",
                 "AWS_SECRET_ACCESS_KEY": "test-secret",
             }), \
             patch("helpers.tick_helpers.mongo", mock_mongo), \
             patch("helpers.tick_helpers.os.getcwd", return_value=str(tmp_path)), \
             patch("helpers.tick_helpers.upload_to_s3") as upload, \
             patch("helpers.discord_post_helpers.post_discord_message", return_value={"ok": True}):
            full_summary_path = th.give_tick_summary("player summary", "full summary", sections)

        timestamp = os.path.basename(full_summary_path).replace("full_tick_summary_", "").replace(".txt", "")
        sidecar_path = os.path.join(os.path.dirname(full_summary_path), f"tick_summary_sections_{timestamp}.json")
        assert os.path.exists(sidecar_path)
        with open(sidecar_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved == [["Nation Stability Tick", "<@111> Taika: stability rose.\n"]]

        # S3 upload must cover all three files once a sections sidecar exists,
        # not just the two .txt ones.
        uploaded_keys = [call.args[1] for call in upload.call_args_list]
        assert any(k.endswith(f"tick_summary_sections_{timestamp}.json") for k in uploaded_keys)

    def test_no_sidecar_is_written_when_there_are_no_sections(self, mock_mongo, tmp_path):
        """era_tick's call site (and the error-handling caller) pass no
        sections at all -- give_tick_summary must not create an empty/
        placeholder sidecar for them."""
        with patch("helpers.tick_helpers.mongo", mock_mongo), \
             patch("helpers.tick_helpers.os.getcwd", return_value=str(tmp_path)), \
             patch("helpers.tick_helpers.upload_to_s3"):
            th.give_tick_summary("player summary", "full summary")

        sidecar_files = [f for f in os.listdir(tmp_path / "summaries") if f.startswith("tick_summary_sections_")]
        assert sidecar_files == []
