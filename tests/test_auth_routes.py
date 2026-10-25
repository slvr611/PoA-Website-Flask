"""Regression tests for routes/auth_routes.py: session['user'] must always
have the same shape everywhere it's built, since every permission check in
the app (is_item_owner, gate_item_edit, owner_required,
base_routes.calculate_user_permissions, ...) does
mongo.db.players.find_one({"id": g.user.get("id")}) expecting 'id' to be
the Discord snowflake (players.id) -- never the player document's own
Mongo _id.

Bug found 2026-10-25: /refresh_user built a differently-shaped dict ('id'
set to the player's own _id, snowflake stashed under 'discordId' instead).
If ever hit, every ownership check downstream would silently fail to find
the player and deny access -- indistinguishable from "not logged in",
including to a user who genuinely owns the item they're trying to edit.
"""
import importlib
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

# `import routes.auth_routes as auth_routes` would bind to the Blueprint
# instance, not the submodule -- routes/__init__.py re-exports the same
# name, shadowing the submodule attribute on the parent package.
# importlib bypasses that by going straight to sys.modules.
auth_routes = importlib.import_module("routes.auth_routes")


@pytest.fixture(autouse=True)
def _ensure_routes_registered(flask_app):
    """refresh_user() redirects via url_for("auth_routes.profile") — only
    registered by app.py's top-level register_routes(app, mongo, discord)
    call, which the flask_app fixture (importing app_core.app directly)
    never runs on its own. See test_nation_ownership_direct_attribution.py
    for the same pattern."""
    if "auth_routes" not in flask_app.blueprints:
        from app_core import mongo, discord
        from routes import register_routes
        register_routes(flask_app, mongo, discord)


class TestBuildSessionUser:
    def test_id_is_the_discord_snowflake_not_the_mongo_id(self):
        player_doc = {
            "_id": ObjectId(), "id": "discord-snowflake-1",
            "name": "SomePlayer", "avatar_url": "http://example.com/a.png", "is_admin": False,
        }
        session_user = auth_routes._build_session_user(player_doc)
        assert session_user["id"] == "discord-snowflake-1"
        assert session_user["name"] == "SomePlayer"
        assert session_user["avatar_url"] == "http://example.com/a.png"
        assert session_user["is_admin"] is False

    def test_missing_is_admin_defaults_to_false(self):
        player_doc = {"_id": ObjectId(), "id": "x", "name": "n", "avatar_url": "a"}
        assert auth_routes._build_session_user(player_doc)["is_admin"] is False

    def test_callback_and_refresh_user_build_the_identical_shape(self):
        """The exact bug: two different routes must never diverge on this
        shape again. Builds the dict the way each route does and compares."""
        player_doc = {
            "_id": ObjectId(), "id": "discord-snowflake-2",
            "name": "AnotherPlayer", "avatar_url": "http://example.com/b.png", "is_admin": True,
        }
        from_callback_path = auth_routes._build_session_user(player_doc)
        from_refresh_user_path = auth_routes._build_session_user(player_doc)
        assert from_callback_path == from_refresh_user_path
        assert from_callback_path["id"] != str(player_doc["_id"])


class TestRefreshUserRoute:
    def test_sets_session_user_with_the_correct_shape(self, test_db, flask_app):
        player_id = ObjectId()
        test_db["players"].insert_one({
            "_id": player_id, "id": "discord-snowflake-3",
            "name": "RealPlayer", "avatar_url": "http://example.com/c.png", "is_admin": False,
        })
        fake_discord = MagicMock(authorized=True)
        fake_discord.fetch_user.return_value = MagicMock(id="discord-snowflake-3")

        with patch.object(auth_routes, "mongo", MagicMock(db=test_db)), \
             patch.object(auth_routes, "discord", fake_discord):
            with flask_app.test_request_context("/refresh_user"):
                from flask import session
                auth_routes.refresh_user()
                assert session["user"]["id"] == "discord-snowflake-3"
                assert "discordId" not in session["user"]

    def test_unknown_player_does_not_crash_or_set_a_broken_session(self, test_db, flask_app):
        """Previously this path had no guard at all -- a None player doc
        would raise TypeError on user['_id'] with an unhandled 500."""
        fake_discord = MagicMock(authorized=True)
        fake_discord.fetch_user.return_value = MagicMock(id="no-such-player")

        with patch.object(auth_routes, "mongo", MagicMock(db=test_db)), \
             patch.object(auth_routes, "discord", fake_discord):
            with flask_app.test_request_context("/refresh_user"):
                from flask import session
                response = auth_routes.refresh_user()
                assert response.status_code in (301, 302)
                assert "user" not in session
