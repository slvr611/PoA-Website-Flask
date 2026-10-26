"""
Tests for routes/admin_tool_routes.py's post_tick_summary_to_discord -- the
manual "retry posting this already-generated tick summary to Discord"
action, for when tick_helpers.give_tick_summary's automatic post inside the
tick silently failed (see its own try/except) or never ran because
DISCORD_TICK_SUMMARY_CHANNEL_ID wasn't set at tick time.
"""
import importlib
import os
from unittest.mock import patch

import pytest

# See tests/test_assimilation_tool.py for why this must come from
# sys.modules rather than `import routes.admin_tool_routes` directly --
# routes/__init__.py rebinds that package attribute to the Blueprint object.
atr = importlib.import_module("routes.admin_tool_routes")


@pytest.fixture(autouse=True)
def _ensure_routes_registered(flask_app):
    """url_for("admin_tool_routes.*") (used by the route's own redirects)
    is only wired up by app.py's top-level register_routes(app, mongo,
    discord) -- the flask_app fixture imports app_core.app directly, which
    never runs that. Same pattern as test_nation_ownership_direct_attribution.py."""
    if "admin_tool_routes" not in flask_app.blueprints:
        from app_core import mongo, discord
        from routes import register_routes
        register_routes(flask_app, mongo, discord)


class TestPostTickSummaryToDiscord:
    def _run(self, flask_app, filename, tmp_path, content="Line one\nLine two"):
        """Runs the view inside its own request context and returns the
        flashed (category, message) pairs captured from that SAME context --
        get_flashed_messages() only works while a request context is still
        active, so it can't be read after this method returns."""
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()
        (summaries_dir / filename).write_text(content, encoding="utf-8")
        from flask import get_flashed_messages
        with flask_app.test_request_context(
            f"/tick_summaries/{filename}/post_to_discord", method="POST"
        ):
            with patch("os.getcwd", return_value=str(tmp_path)):
                atr.post_tick_summary_to_discord.__wrapped__(filename)
            return get_flashed_messages(with_categories=True)

    def test_posts_successfully_and_flashes_success(self, flask_app, tmp_path):
        with patch.dict(os.environ, {"DISCORD_TICK_SUMMARY_CHANNEL_ID": "123", "DISCORD_BOT_TOKEN": "tok"}), \
             patch("helpers.discord_post_helpers.post_discord_message", return_value={"ok": True}) as mock_post:
            self._run(flask_app, "player_tick_summary_20261026_000000.txt", tmp_path)
        mock_post.assert_called_once_with("123", "Line one\nLine two")

    def test_missing_channel_env_surfaces_that_reason(self, flask_app, tmp_path, monkeypatch):
        monkeypatch.delenv("DISCORD_TICK_SUMMARY_CHANNEL_ID", raising=False)
        messages = self._run(flask_app, "player_tick_summary_20261026_000001.txt", tmp_path)
        assert any("DISCORD_TICK_SUMMARY_CHANNEL_ID is not set" in m for _, m in messages)

    def test_forbidden_error_surfaces_permission_hint(self, flask_app, tmp_path):
        with patch.dict(os.environ, {"DISCORD_TICK_SUMMARY_CHANNEL_ID": "123", "DISCORD_BOT_TOKEN": "tok"}), \
             patch("helpers.discord_post_helpers.post_discord_message", return_value={"ok": False, "error": "forbidden"}):
            messages = self._run(flask_app, "player_tick_summary_20261026_000002.txt", tmp_path)
        assert any("Send Messages permission" in m for _, m in messages)

    def test_empty_summary_is_not_posted(self, flask_app, tmp_path):
        with patch.dict(os.environ, {"DISCORD_TICK_SUMMARY_CHANNEL_ID": "123", "DISCORD_BOT_TOKEN": "tok"}), \
             patch("helpers.discord_post_helpers.post_discord_message") as mock_post:
            messages = self._run(flask_app, "player_tick_summary_20261026_000003.txt", tmp_path, content="   \n\n")
        mock_post.assert_not_called()
        assert any("nothing to post" in m for _, m in messages)

    def test_missing_file_flashes_not_found(self, flask_app, tmp_path):
        from flask import get_flashed_messages
        with flask_app.test_request_context(
            "/tick_summaries/does_not_exist.txt/post_to_discord", method="POST"
        ):
            with patch("os.getcwd", return_value=str(tmp_path)):
                atr.post_tick_summary_to_discord.__wrapped__("does_not_exist.txt")
            messages = get_flashed_messages(with_categories=True)
        assert any("not found locally and S3 is not configured" in m for _, m in messages)
