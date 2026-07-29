"""
Tests for the Assimilation Tool (routes/admin_tool_routes.py:
assimilation_tool_execute), which merges one nation's districts/cities/pops
into another nation.

_calculate_and_attach_fields is mocked out to a passthrough — it's the same
already-proven recalculation step civil_war_execute relies on, so these
tests focus on the tool's own new logic: merging district/city arrays
(including re-issuing colliding item _ids), moving pops, and clearing the
source's transferred items.
"""
import importlib
from unittest.mock import patch
from bson import ObjectId

import mongomock

# routes/__init__.py does `from .admin_tool_routes import admin_tool_routes`,
# which rebinds the `admin_tool_routes` attribute on the `routes` package to
# the Blueprint object — so `import routes.admin_tool_routes as X` (package
# attribute lookup) would resolve to the Blueprint, not the module. Pulling
# from sys.modules avoids that shadowing (see tests/test_change_approval_flash.py
# for the same issue).
atr = importlib.import_module("routes.admin_tool_routes")


def _fake_category_data(test_db):
    empty_schema = {"properties": {}}
    return {
        "nations": {
            "pluralName": "Nations", "singularName": "Nation",
            "database": test_db["nations"], "schema": empty_schema,
        },
    }


class TestAssimilationToolExecute:
    def _run(self, flask_app, test_db, form):
        fake_category_data = _fake_category_data(test_db)
        with flask_app.test_request_context(
            "/assimilation_tool/execute", method="POST", data=form
        ):
            with patch("routes.admin_tool_routes.mongo") as mock_mongo, \
                 patch("helpers.data_helpers.category_data", fake_category_data), \
                 patch("helpers.change_helpers._calculate_and_attach_fields", side_effect=lambda dt, doc: doc):
                mock_mongo.db = test_db
                return atr.assimilation_tool_execute.__wrapped__()

    def test_merges_districts_cities_and_pops(self, flask_app, test_db):
        source_id = ObjectId()
        target_id = ObjectId()
        test_db["nations"].insert_one({
            "_id": source_id, "name": "Loser",
            "districts": [{"_id": "aaaaaaaa", "def_key": "farm"}],
            "cities": [{"_id": "bbbbbbbb", "name": "Old Capital"}],
        })
        test_db["nations"].insert_one({
            "_id": target_id, "name": "Winner",
            "districts": [{"_id": "cccccccc", "def_key": "mine"}],
            "cities": [],
        })
        test_db["pops"].insert_one({"_id": ObjectId(), "nation": str(source_id), "race": "Human"})
        test_db["pops"].insert_one({"_id": ObjectId(), "nation": str(source_id), "race": "Elf"})

        self._run(flask_app, test_db, {
            "source_nation": str(source_id),
            "target_nation": str(target_id),
            "transfer_districts": "on",
            "transfer_cities": "on",
            "transfer_pops": "on",
        })

        source = test_db["nations"].find_one({"_id": source_id})
        target = test_db["nations"].find_one({"_id": target_id})

        assert source["districts"] == []
        assert source["cities"] == []
        assert len(target["districts"]) == 2
        assert len(target["cities"]) == 1
        assert {d["_id"] for d in target["districts"]} == {"aaaaaaaa", "cccccccc"}

        assert test_db["pops"].count_documents({"nation": str(target_id)}) == 2
        assert test_db["pops"].count_documents({"nation": str(source_id)}) == 0

    def test_colliding_item_id_is_reissued_not_dropped(self, flask_app, test_db):
        source_id = ObjectId()
        target_id = ObjectId()
        # Both nations happen to have a district with the same _id.
        test_db["nations"].insert_one({
            "_id": source_id, "name": "Loser",
            "districts": [{"_id": "dupeid01", "def_key": "farm"}],
            "cities": [],
        })
        test_db["nations"].insert_one({
            "_id": target_id, "name": "Winner",
            "districts": [{"_id": "dupeid01", "def_key": "mine"}],
            "cities": [],
        })

        self._run(flask_app, test_db, {
            "source_nation": str(source_id),
            "target_nation": str(target_id),
            "transfer_districts": "on",
        })

        target = test_db["nations"].find_one({"_id": target_id})
        assert len(target["districts"]) == 2, "colliding item was dropped instead of re-issued a new _id"
        ids = [d["_id"] for d in target["districts"]]
        assert len(set(ids)) == 2, "both districts ended up with the same _id after merge"
        # The original (target's own) district must not have been renamed.
        assert "dupeid01" in ids
        def_keys = {d["_id"]: d["def_key"] for d in target["districts"]}
        assert def_keys["dupeid01"] == "mine"

    def test_unchecked_categories_are_not_transferred(self, flask_app, test_db):
        source_id = ObjectId()
        target_id = ObjectId()
        test_db["nations"].insert_one({
            "_id": source_id, "name": "Loser",
            "districts": [{"_id": "aaaaaaaa", "def_key": "farm"}],
            "cities": [{"_id": "bbbbbbbb", "name": "Old Capital"}],
        })
        test_db["nations"].insert_one({"_id": target_id, "name": "Winner", "districts": [], "cities": []})
        test_db["pops"].insert_one({"_id": ObjectId(), "nation": str(source_id), "race": "Human"})

        self._run(flask_app, test_db, {
            "source_nation": str(source_id),
            "target_nation": str(target_id),
            # nothing checked
        })

        source = test_db["nations"].find_one({"_id": source_id})
        target = test_db["nations"].find_one({"_id": target_id})
        assert len(source["districts"]) == 1
        assert len(source["cities"]) == 1
        assert target["districts"] == []
        assert target["cities"] == []
        assert test_db["pops"].count_documents({"nation": str(source_id)}) == 1

    def test_same_nation_rejected(self, flask_app, test_db):
        nation_id = ObjectId()
        test_db["nations"].insert_one({"_id": nation_id, "name": "Solo", "districts": [], "cities": []})

        fake_category_data = _fake_category_data(test_db)
        with flask_app.test_request_context(
            "/assimilation_tool/execute", method="POST",
            data={"source_nation": str(nation_id), "target_nation": str(nation_id)},
        ):
            with patch("routes.admin_tool_routes.mongo") as mock_mongo, \
                 patch("helpers.data_helpers.category_data", fake_category_data):
                mock_mongo.db = test_db
                from flask import get_flashed_messages
                atr.assimilation_tool_execute.__wrapped__()
                messages = get_flashed_messages(with_categories=True)
                assert any(cat == "error" for cat, _ in messages)

        # Nothing should have changed.
        nation = test_db["nations"].find_one({"_id": nation_id})
        assert nation["districts"] == []
