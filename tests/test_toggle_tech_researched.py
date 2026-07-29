"""
Tests for the non-player-admin-only "toggle tech researched" override
(routes/nation_routes.py: toggle_tech_researched / _is_non_player_admin_or_admin).

This bypasses the normal investment mechanic and change-request system
entirely, so these tests check both halves of the requirement: only a full
admin or a non-player admin can flip it, and doing so writes directly (no
Pending change created) plus recalculates the nation.
"""
import importlib
from unittest.mock import patch, MagicMock
from bson import ObjectId

import mongomock

# routes/__init__.py does `from .nation_routes import nation_routes`, which
# rebinds the `nation_routes` attribute on the `routes` package to the
# Blueprint object — pulling from sys.modules avoids that shadowing (same
# issue documented in tests/test_change_approval_flash.py).
nr = importlib.import_module("routes.nation_routes")


def _fake_category_data(test_db):
    empty_schema = {"properties": {}, "external_calculation_requirements": {}}
    return {
        "nations": {
            "pluralName": "Nations", "singularName": "Nation",
            "database": test_db["nations"], "schema": empty_schema,
        },
    }


class TestToggleTechResearchedPermissions:
    def _call(self, flask_app, test_db, nation_name, tech_key, user):
        fake_mongo = MagicMock()
        fake_mongo.db = test_db
        with flask_app.test_request_context(
            f"/nations/item/{nation_name}/tech/{tech_key}/toggle_researched", method="POST"
        ):
            with patch.object(nr, "mongo", fake_mongo), \
                 patch("helpers.change_helpers.category_data", _fake_category_data(test_db)), \
                 patch("helpers.change_helpers._calculate_and_attach_fields", side_effect=lambda dt, doc: doc), \
                 patch.object(nr, "g") as mock_g:
                mock_g.user = user
                mock_g.is_non_player_admin = bool(user) and user.get("is_non_player_admin", False)
                return nr.toggle_tech_researched(nation_name, tech_key)

    def test_regular_player_is_rejected(self, flask_app, test_db):
        nation_id = ObjectId()
        test_db["nations"].insert_one({"_id": nation_id, "name": "Testland", "technologies": {}})

        self._call(flask_app, test_db, "Testland", "advanced_numerals", {"id": "p1", "is_admin": False})

        nation = test_db["nations"].find_one({"_id": nation_id})
        assert nation.get("technologies", {}).get("advanced_numerals") is None, (
            "a regular player was able to toggle a tech's researched flag"
        )

    def test_full_admin_can_toggle(self, flask_app, test_db):
        nation_id = ObjectId()
        test_db["nations"].insert_one({"_id": nation_id, "name": "Testland", "technologies": {}})

        self._call(flask_app, test_db, "Testland", "advanced_numerals", {"id": "admin1", "is_admin": True})

        nation = test_db["nations"].find_one({"_id": nation_id})
        assert nation["technologies"]["advanced_numerals"]["researched"] is True

    def test_non_player_admin_can_toggle(self, flask_app, test_db):
        nation_id = ObjectId()
        test_db["nations"].insert_one({"_id": nation_id, "name": "Testland", "technologies": {}})

        self._call(flask_app, test_db, "Testland", "advanced_numerals",
                    {"id": "npa1", "is_admin": False, "is_non_player_admin": True})

        nation = test_db["nations"].find_one({"_id": nation_id})
        assert nation["technologies"]["advanced_numerals"]["researched"] is True

    def test_toggle_is_a_flip_not_always_on(self, flask_app, test_db):
        nation_id = ObjectId()
        test_db["nations"].insert_one({
            "_id": nation_id, "name": "Testland",
            "technologies": {"advanced_numerals": {"researched": True, "invested": 7, "investing": 0, "cost": 7}},
        })

        self._call(flask_app, test_db, "Testland", "advanced_numerals", {"id": "admin1", "is_admin": True})

        nation = test_db["nations"].find_one({"_id": nation_id})
        entry = nation["technologies"]["advanced_numerals"]
        assert entry["researched"] is False
        assert entry["invested"] == 7  # untouched fields must survive the round trip
