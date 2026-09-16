"""
Regression tests for a real incident: an earlier version of
scripts/fix_disconnected_districts.py only excluded player-owned nations
from Phase 3 (sync_nation_districts/sync_nation_cities calls, which are
AI-scoped internally) but NOT from Phases 1, 4, and 5 — which silently
relocated or deduplicated districts/cities in at least 12 player-owned
nations before this was caught. Every phase must exclude player_ids now,
matching the established convention elsewhere in the codebase
(fix_city_and_capital_placement: "Player-owned cities are NEVER moved";
the /admin/sync_cities, /admin/sync_districts routes; and
reconcile_map_and_ai_nations.py).

These tests construct one player-owned nation and one AI nation, each with
the same fixable issue, and assert the player nation is left completely
untouched while the AI nation is fixed.
"""
from unittest.mock import MagicMock, patch

import mongomock
from bson import ObjectId

import scripts.fix_disconnected_districts as fdd


def _fake_mongo(test_db):
    m = MagicMock()
    m.db = test_db
    return m


class TestPhase1ExcludesPlayerNations:
    def test_player_nations_citadel_is_never_refunded_or_removed(self):
        test_db = mongomock.MongoClient()["poa_test"]
        player_id = ObjectId()
        ai_id = ObjectId()
        test_db["nations"].insert_one({
            "_id": player_id, "name": "PlayerNation",
            "cities": [{"_id": "c1", "type": "vandadorian_citadel", "name": ""}],
            "resource_storage": {},
        })
        test_db["nations"].insert_one({
            "_id": ai_id, "name": "AiNation",
            "cities": [{"_id": "c2", "type": "vandadorian_citadel", "name": ""}],
            "resource_storage": {},
        })
        test_db["hex_map_tiles"].insert_one({
            "owner": "PlayerNation", "q": 0, "r": 0,
            "city": {"id": "c1", "type": "vandadorian_citadel"},
        })
        test_db["hex_map_tiles"].insert_one({
            "owner": "AiNation", "q": 1, "r": 1,
            "city": {"id": "c2", "type": "vandadorian_citadel"},
        })

        fake_cities_json = {"cities": {"vandadorian_citadel": {"cost": {"stone": 15}}}}
        with patch.object(fdd, "mongo", _fake_mongo(test_db)), \
             patch.object(fdd, "json_data", fake_cities_json):
            actions, lost_capital, removed_tiles = fdd.phase1_refund_illegal_vandadorian_citadels(
                dry_run=False, player_ids={player_id}
            )

        acted_on = {a["nation"] for a in actions}
        assert acted_on == {"AiNation"}

        player_nation = test_db["nations"].find_one({"_id": player_id})
        assert player_nation["cities"] == [{"_id": "c1", "type": "vandadorian_citadel", "name": ""}]
        player_tile = test_db["hex_map_tiles"].find_one({"owner": "PlayerNation"})
        assert player_tile["city"] is not None

        ai_nation = test_db["nations"].find_one({"_id": ai_id})
        assert ai_nation["cities"] == []
        ai_tile = test_db["hex_map_tiles"].find_one({"owner": "AiNation"})
        assert "city" not in ai_tile or ai_tile.get("city") is None


class TestPhase4ExcludesPlayerNations:
    def test_player_nations_duplicate_is_never_resolved(self):
        test_db = mongomock.MongoClient()["poa_test"]
        player_id = ObjectId()
        ai_id = ObjectId()
        test_db["nations"].insert_one({
            "_id": player_id, "name": "PlayerNation",
            "districts": [{"_id": "d1", "def_key": "forge"}],
        })
        test_db["nations"].insert_one({
            "_id": ai_id, "name": "AiNation",
            "districts": [{"_id": "d2", "def_key": "forge"}],
        })
        # Same duplicate shape for both: one id claimed on two tiles.
        test_db["hex_map_tiles"].insert_one({
            "owner": "PlayerNation", "q": 0, "r": 0,
            "district": {"id": "d1", "def_key": "forge"},
        })
        test_db["hex_map_tiles"].insert_one({
            "owner": "PlayerNation", "q": 1, "r": 0,
            "district": {"id": "d1", "def_key": "forge"},
        })
        test_db["hex_map_tiles"].insert_one({
            "owner": "AiNation", "q": 0, "r": 0,
            "district": {"id": "d2", "def_key": "forge"},
        })
        test_db["hex_map_tiles"].insert_one({
            "owner": "AiNation", "q": 1, "r": 0,
            "district": {"id": "d2", "def_key": "forge"},
        })

        with patch.object(fdd, "mongo", _fake_mongo(test_db)):
            actions = fdd.phase4_resolve_duplicates(dry_run=False, player_ids={player_id})

        acted_on = {a["nation"] for a in actions}
        assert acted_on == {"AiNation"}

        # Player nation's duplicate is untouched — both tiles still set.
        player_tiles = list(test_db["hex_map_tiles"].find({"owner": "PlayerNation"}))
        assert sum(1 for t in player_tiles if t.get("district")) == 2

        # AI nation's duplicate was resolved down to one tile.
        ai_tiles = list(test_db["hex_map_tiles"].find({"owner": "AiNation"}))
        assert sum(1 for t in ai_tiles if t.get("district")) == 1


class TestPhase5ExcludesPlayerNations:
    def test_player_nations_disconnected_district_is_never_relocated(self):
        test_db = mongomock.MongoClient()["poa_test"]
        player_id = ObjectId()
        test_db["nations"].insert_one({
            "_id": player_id, "name": "PlayerNation",
            "districts": [{"_id": "d1", "def_key": "forge"}],
            "resource_storage": {}, "government_type": "Standard",
        })
        # A district with zero adjacent buildings — would normally be
        # flagged and relocated (or refunded) by phase 5.
        test_db["hex_map_tiles"].insert_one({
            "owner": "PlayerNation", "q": 0, "r": 0, "terrain": "plains",
            "district": {"id": "d1", "def_key": "forge"},
        })

        with patch.object(fdd, "mongo", _fake_mongo(test_db)):
            moved, refunded = fdd.phase5_relocate_disconnected_districts(
                dry_run=False, player_ids={player_id}
            )

        assert moved == []
        assert refunded == []
        tile = test_db["hex_map_tiles"].find_one({"owner": "PlayerNation"})
        assert tile["district"]["id"] == "d1"
