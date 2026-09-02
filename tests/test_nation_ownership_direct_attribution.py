"""
Regression tests for a real reported bug: Risendiablo owns Nisilon directly
(nation.players), with no character ruling it — but was getting "You don't
have permission to access this nation" when editing jobs, and Nisilon never
appeared in his Quick Links.

Root cause, in two places: both helpers.auth_helpers.owner_required (gates
routes like the jobs-edit page) and helpers.data_helpers.get_user_entities
(builds the Quick Links list) determined "which nations does this player
have access to" using ONLY characters with ruling_nation_org set — never
checking direct attribution via nation.players. Every other ownership check
in this codebase (helpers.visibility_helpers.is_item_owner,
calculations.visibility.get_viewer_nations, routes.admin_tool_routes.
_get_player_nation_ids) already treats both attribution paths as valid;
these two just hadn't been updated to match.
"""
import pytest
from unittest.mock import patch
from bson import ObjectId

import helpers.auth_helpers as auth_helpers
import helpers.data_helpers as data_helpers


@pytest.fixture(autouse=True)
def _ensure_routes_registered(flask_app):
    """base_routes.home (the redirect target on denied access) is only
    registered by app.py's top-level register_routes(app, mongo, discord)
    call — the flask_app fixture imports app_core.app directly, which never
    runs that, so url_for("base_routes.home") 404s/BuildErrors by default."""
    if "base_routes" not in flask_app.blueprints:
        from app_core import mongo, discord
        from routes import register_routes
        register_routes(flask_app, mongo, discord)


def _seed_player_and_nation(test_db, via="direct"):
    """via="direct": nation.players lists the player, no ruling character.
    via="ruling_character": a character with ruling_nation_org, no direct
    attribution. via="both": both paths point at the SAME nation."""
    player_id = ObjectId()
    nation_id = ObjectId()
    test_db["players"].insert_one({"_id": player_id, "id": "user-1"})

    nation_doc = {"_id": nation_id, "name": "Nisilon"}
    if via in ("direct", "both"):
        nation_doc["players"] = [str(player_id)]
    test_db["nations"].insert_one(nation_doc)

    if via in ("ruling_character", "both"):
        test_db["characters"].insert_one({
            "_id": ObjectId(), "name": "Elisho",
            "player": str(player_id), "ruling_nation_org": str(nation_id),
        })

    return player_id, nation_id


class TestOwnerRequiredDirectNationAttribution:
    def _call_decorated(self, flask_app, test_db, item_ref="Nisilon"):
        @auth_helpers.owner_required("nations")
        def view(item_ref):
            return "OK"

        with flask_app.test_request_context(f"/nations/edit_jobs/{item_ref}"):
            from flask import g
            g.user = {"id": "user-1"}
            with patch.object(auth_helpers, "mongo", type("M", (), {"db": test_db})()):
                return view(item_ref=item_ref)

    def test_direct_attribution_with_no_ruling_character_is_allowed(self, flask_app, test_db):
        self._seed = _seed_player_and_nation(test_db, via="direct")
        result = self._call_decorated(flask_app, test_db)
        assert result == "OK"

    def test_ruling_character_attribution_still_works(self, flask_app, test_db):
        """Regression guard: the pre-existing, already-working path must
        keep working after this fix."""
        _seed_player_and_nation(test_db, via="ruling_character")
        result = self._call_decorated(flask_app, test_db)
        assert result == "OK"

    def test_both_paths_present_is_allowed(self, flask_app, test_db):
        _seed_player_and_nation(test_db, via="both")
        result = self._call_decorated(flask_app, test_db)
        assert result == "OK"

    def test_unrelated_player_is_still_denied(self, flask_app, test_db):
        """Sanity: the fix must not accidentally grant everyone access."""
        _seed_player_and_nation(test_db, via="direct")
        test_db["players"].delete_many({})
        test_db["players"].insert_one({"_id": ObjectId(), "id": "user-1"})
        result = self._call_decorated(flask_app, test_db)
        assert result != "OK"  # redirected home, not the view's return value


class TestGetUserEntitiesDirectNationAttribution:
    def _call(self, flask_app, test_db):
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": "user-1"}
            with patch.object(data_helpers, "mongo", type("M", (), {"db": test_db})()):
                return data_helpers.get_user_entities()

    def test_directly_owned_nation_with_no_ruling_character_appears(self, flask_app, test_db):
        _seed_player_and_nation(test_db, via="direct")
        entities = self._call(flask_app, test_db)
        assert [n["name"] for n in entities["nations"]] == ["Nisilon"]

    def test_ruling_character_nation_still_appears(self, flask_app, test_db):
        _seed_player_and_nation(test_db, via="ruling_character")
        entities = self._call(flask_app, test_db)
        assert [n["name"] for n in entities["nations"]] == ["Nisilon"]

    def test_both_paths_do_not_duplicate_the_nation(self, flask_app, test_db):
        _seed_player_and_nation(test_db, via="both")
        entities = self._call(flask_app, test_db)
        assert [n["name"] for n in entities["nations"]] == ["Nisilon"]
