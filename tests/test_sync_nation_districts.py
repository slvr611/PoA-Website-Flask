"""
Tests for helpers.ai_decision_helpers.sync_nation_districts — reconciles a
nation's `districts` array against district objects placed on its owned
map tiles (mirrors the existing sync_nation_cities exactly).

Confirmed live via scripts/reconcile_map_and_ai_nations.py that this
mismatch is real and common: 88 of ~190 AI nations had at least one
district/city that existed on only one side (the nation page or the map,
not both).
"""
from unittest.mock import patch
from bson import ObjectId

import helpers.ai_decision_helpers as adh


def _patched_mongo(test_db):
    fake_mongo = type("FakeMongo", (), {"db": test_db})()
    return patch.object(adh, "mongo", fake_mongo)


class TestSyncNationDistrictsMapToNation:
    def test_tile_district_missing_from_nation_gets_added(self, test_db):
        nation_id = ObjectId()
        nation = {"_id": nation_id, "name": "Test Nation", "districts": []}
        test_db["hex_map_tiles"].insert_one({
            "q": 1, "r": 1, "owner": "Test Nation",
            "district": {"id": "abc12345", "def_key": "farm", "display_name": "Farm", "type": ""},
            "node": {"resource_type": "iron"},
        })
        with _patched_mongo(test_db):
            report = adh.sync_nation_districts(nation, dry_run=True)

        assert report["added_to_nation"] == [{"id": "abc12345", "def_key": "farm", "node": "iron"}]

    def test_apply_false_writes_the_nation_document(self, test_db):
        nation_id = ObjectId()
        test_db["nations"].insert_one({"_id": nation_id, "name": "Test Nation", "districts": []})
        nation = test_db["nations"].find_one({"_id": nation_id})
        test_db["hex_map_tiles"].insert_one({
            "q": 1, "r": 1, "owner": "Test Nation",
            "district": {"id": "abc12345", "def_key": "farm", "display_name": "Farm", "type": ""},
            "node": None,
        })
        with _patched_mongo(test_db):
            adh.sync_nation_districts(nation, dry_run=False)

        updated = test_db["nations"].find_one({"_id": nation_id})
        assert updated["districts"] == [{"_id": "abc12345", "def_key": "farm", "node": "", "upgrades": []}]

    def test_fills_blank_slot_before_appending(self, test_db):
        nation_id = ObjectId()
        test_db["nations"].insert_one({
            "_id": nation_id, "name": "Test Nation",
            "districts": [{"_id": "slot1"}],  # blank placeholder slot
        })
        nation = test_db["nations"].find_one({"_id": nation_id})
        test_db["hex_map_tiles"].insert_one({
            "q": 1, "r": 1, "owner": "Test Nation",
            "district": {"id": "abc12345", "def_key": "farm", "display_name": "Farm", "type": ""},
            "node": None,
        })
        with _patched_mongo(test_db):
            adh.sync_nation_districts(nation, dry_run=False)

        updated = test_db["nations"].find_one({"_id": nation_id})
        assert len(updated["districts"]) == 1
        assert updated["districts"][0]["_id"] == "abc12345"

    def test_blank_slot_fill_and_append_together_do_not_conflict(self, test_db):
        """Real production bug: when a blank slot needs filling (,$set on
        "districts.N.field") AND another map-only district needs appending
        ($push on "districts") in the SAME apply call, MongoDB rejects a
        single update_doc combining both — error 40, "Updating the path
        'districts' would create a conflict at 'districts'" — since $set on
        a sub-path and $push on the parent path overlap. Both writes must
        succeed when issued as two separate update_one calls."""
        nation_id = ObjectId()
        test_db["nations"].insert_one({
            "_id": nation_id, "name": "Test Nation",
            "districts": [{"_id": "slot1"}],  # one blank placeholder slot
        })
        nation = test_db["nations"].find_one({"_id": nation_id})
        test_db["hex_map_tiles"].insert_many([
            {"q": 1, "r": 1, "owner": "Test Nation",
             "district": {"id": "abc12345", "def_key": "farm", "display_name": "Farm", "type": ""},
             "node": None},
            {"q": 2, "r": 1, "owner": "Test Nation",
             "district": {"id": "def67890", "def_key": "quarry", "display_name": "Quarry", "type": ""},
             "node": None},
        ])
        with _patched_mongo(test_db):
            adh.sync_nation_districts(nation, dry_run=False)

        updated = test_db["nations"].find_one({"_id": nation_id})
        assert len(updated["districts"]) == 2
        ids = {d["_id"] for d in updated["districts"]}
        assert ids == {"abc12345", "def67890"}

    def test_imperial_district_claim_is_ignored(self, test_db):
        """Imperial quarter districts are a separate single-instance
        mechanism (nation.imperial_district) unrelated to the districts
        array — must never be treated as a missing regular district."""
        nation = {"_id": ObjectId(), "name": "Test Nation", "districts": []}
        test_db["hex_map_tiles"].insert_one({
            "q": 1, "r": 1, "owner": "Test Nation",
            "district": {"id": "imp12345", "def_key": "", "display_name": "Imperial Quarter", "imperial": True},
        })
        with _patched_mongo(test_db):
            report = adh.sync_nation_districts(nation, dry_run=True)

        assert report["added_to_nation"] == []

    def test_already_present_district_is_not_duplicated(self, test_db):
        nation = {
            "_id": ObjectId(), "name": "Test Nation",
            "districts": [{"_id": "abc12345", "def_key": "farm", "node": "iron", "upgrades": []}],
        }
        test_db["hex_map_tiles"].insert_one({
            "q": 1, "r": 1, "owner": "Test Nation",
            "district": {"id": "abc12345", "def_key": "farm", "display_name": "Farm", "type": ""},
        })
        with _patched_mongo(test_db):
            report = adh.sync_nation_districts(nation, dry_run=True)

        assert report["added_to_nation"] == []


class TestSyncNationDistrictsNationToMap:
    def test_unplaceable_when_district_def_missing(self, test_db):
        nation = {
            "_id": ObjectId(), "name": "Test Nation", "government_type": "Standard",
            "districts": [{"_id": "abc12345", "def_key": "nonexistent", "node": "", "upgrades": []}],
        }
        with _patched_mongo(test_db):
            report = adh.sync_nation_districts(nation, dry_run=True)

        assert report["placed_on_map"] == []
        assert report["unplaceable"] == [{
            "id": "abc12345", "def_key": "nonexistent", "reason": "no matching district_defs entry",
        }]

    def test_places_on_a_legal_adjacent_tile(self, test_db):
        nation = {
            "_id": ObjectId(), "name": "Test Nation", "government_type": "Standard",
            "districts": [{"_id": "abc12345", "def_key": "farm", "node": "", "upgrades": []}],
        }
        test_db["district_defs"].insert_one({
            "key": "farm", "display_name": "Farm", "tile_requirement": "land", "modifiers": [],
        })
        # An existing building tile plus an adjacent empty tile — the only
        # legal placement candidate.
        owned_tiles = [
            {"q": 0, "r": 0, "owner": "Test Nation", "terrain": "plains",
             "district": {"id": "existing1", "def_key": "quarry", "display_name": "Quarry", "type": ""}},
            {"q": 1, "r": 0, "owner": "Test Nation", "terrain": "plains"},
        ]
        test_db["hex_map_tiles"].insert_many([dict(t) for t in owned_tiles])
        # owned_tiles is passed explicitly so _compute_legal_placement (in
        # calculations/field_calculations.py, a separate module with its own
        # local `from app_core import mongo` import unaffected by patching
        # adh.mongo here) never needs to query anything itself.
        with _patched_mongo(test_db):
            report = adh.sync_nation_districts(nation, dry_run=True, owned_tiles=owned_tiles)

        assert report["unplaceable"] == []
        assert len(report["placed_on_map"]) == 1
        assert report["placed_on_map"][0]["id"] == "abc12345"
        assert report["placed_on_map"][0]["coord"] == [1, 0]

    def test_apply_false_writes_the_tile(self, test_db):
        nation = {
            "_id": ObjectId(), "name": "Test Nation", "government_type": "Standard",
            "districts": [{"_id": "abc12345", "def_key": "farm", "node": "", "upgrades": []}],
        }
        test_db["district_defs"].insert_one({
            "key": "farm", "display_name": "Farm", "tile_requirement": "land", "modifiers": [],
        })
        owned_tiles = [
            {"q": 0, "r": 0, "owner": "Test Nation", "terrain": "plains",
             "district": {"id": "existing1", "def_key": "quarry", "display_name": "Quarry", "type": ""}},
            {"q": 1, "r": 0, "owner": "Test Nation", "terrain": "plains"},
        ]
        test_db["hex_map_tiles"].insert_many([dict(t) for t in owned_tiles])
        with _patched_mongo(test_db):
            adh.sync_nation_districts(nation, dry_run=False, owned_tiles=owned_tiles)

        tile = test_db["hex_map_tiles"].find_one({"q": 1, "r": 0})
        assert tile["district"]["id"] == "abc12345"
        assert tile["district"]["def_key"] == "farm"

    def test_blank_slots_and_legacy_type_districts_are_skipped(self, test_db):
        nation = {
            "_id": ObjectId(), "name": "Test Nation", "government_type": "Standard",
            "districts": [
                {"_id": "blank1"},
                {"_id": "legacy1", "type": "old_style", "node": "", "era": 1},
            ],
        }
        with _patched_mongo(test_db):
            report = adh.sync_nation_districts(nation, dry_run=True)

        assert report["placed_on_map"] == []
        assert report["unplaceable"] == []

    def test_nomadic_nation_is_skipped_entirely(self, test_db):
        nation = {
            "_id": ObjectId(), "name": "Test Nation", "government_type": "Standard",
            "is_nomadic": True,
            "districts": [{"_id": "abc12345", "def_key": "farm", "node": "", "upgrades": []}],
        }
        test_db["district_defs"].insert_one({
            "key": "farm", "display_name": "Farm", "tile_requirement": "land", "modifiers": [],
        })
        with _patched_mongo(test_db):
            report = adh.sync_nation_districts(nation, dry_run=True)

        assert report["placed_on_map"] == []
        assert report["unplaceable"] == []
        assert report["skipped_nomadic"] == [{"id": "abc12345", "def_key": "farm"}]
