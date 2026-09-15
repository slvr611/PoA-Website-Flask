"""
Tests for helpers.tick_helpers.ai_duplicate_building_cleanup_tick.

An AI nation can legitimately end up with two DISTINCT (different _id)
entries of a district/city type that isn't supposed to allow more than one
— confirmed live via the Assimilation Tool (routes/admin_tool_routes.py's
assimilation_tool_execute), which merges two nations' districts/cities
arrays with no uniqueness check at all. This is a different bug class from
the same-id-on-two-tiles map corruption fixed earlier (see
sync_nation_cities/sync_nation_districts's world_city_ids/
world_district_ids guards) — this tick keeps exactly one real instance and
dismantles the rest for a partial refund, every tick, so the state never
lingers.
"""
from unittest.mock import MagicMock, patch

import helpers.tick_helpers as th


def _patched_mongo(test_db):
    return patch.object(th, "mongo", MagicMock(db=test_db))


def _ai_nation(**overrides):
    nation = {
        "_id": "nation-1", "name": "Test AI Nation", "temperament": "Aggressive",
        "districts": [], "cities": [], "resource_storage": {}, "nation_resource_capacity": {},
    }
    nation.update(overrides)
    return nation


class TestPlayerNationsAreSkipped:
    def test_player_nation_is_never_touched(self, test_db):
        old_nation = _ai_nation(temperament="Player", districts=[
            {"_id": "d1", "def_key": "forge", "node": "", "upgrades": []},
            {"_id": "d2", "def_key": "forge", "node": "", "upgrades": []},
        ])
        new_nation = dict(old_nation)

        with _patched_mongo(test_db):
            result = th.ai_duplicate_building_cleanup_tick(old_nation, new_nation, {})

        assert result == ""
        assert len(new_nation["districts"]) == 2


class TestDuplicateDistricts:
    def test_duplicate_non_multi_district_is_dismantled_and_refunded(self, test_db):
        test_db["district_defs"].insert_one({
            "key": "forge", "display_name": "Forge", "allow_multiple": False,
            "cost": {"wood": 3, "stone": 9, "iron": 2},
        })
        old_nation = _ai_nation(districts=[
            {"_id": "d1", "def_key": "forge", "node": "", "upgrades": []},
            {"_id": "d2", "def_key": "forge", "node": "", "upgrades": []},
        ])
        new_nation = dict(old_nation)
        pending_tiles = []
        # Both placed on plain (terrain-valid) land tiles so the "keep the
        # terrain-valid copy" preference doesn't override array order here
        # — that specific behavior gets its own test below.
        test_db["hex_map_tiles"].insert_one({
            "owner": "Test AI Nation", "q": 0, "r": 0, "terrain": "plains",
            "district": {"id": "d1", "def_key": "forge"},
        })
        test_db["hex_map_tiles"].insert_one({
            "owner": "Test AI Nation", "q": 1, "r": 1, "terrain": "plains",
            "district": {"id": "d2", "def_key": "forge"},
        })

        with _patched_mongo(test_db):
            result = th.ai_duplicate_building_cleanup_tick(
                old_nation, new_nation, {}, pending_tiles=pending_tiles
            )

        remaining_ids = {d["_id"] for d in new_nation["districts"]}
        assert remaining_ids == {"d1"}
        # 1/4 of {wood:3, stone:9, iron:2} rounded up -> wood:1, stone:3, iron:1
        assert new_nation["resource_storage"] == {"wood": 1, "stone": 3, "iron": 1}
        assert "dismantled duplicate district" in result
        assert len(pending_tiles) == 1
        assert pending_tiles[0]["set"]["district"] is None

    def test_allow_multiple_district_is_left_alone(self, test_db):
        test_db["district_defs"].insert_one({
            "key": "outpost", "display_name": "Outpost", "allow_multiple": True,
            "cost": {"wood": 4}, "map_count": 2,
        })
        old_nation = _ai_nation(districts=[
            {"_id": "d1", "def_key": "outpost", "node": "", "upgrades": []},
            {"_id": "d2", "def_key": "outpost", "node": "", "upgrades": []},
        ])
        new_nation = dict(old_nation)

        with _patched_mongo(test_db):
            result = th.ai_duplicate_building_cleanup_tick(old_nation, new_nation, {})

        assert result == ""
        assert {d["_id"] for d in new_nation["districts"]} == {"d1", "d2"}
        assert new_nation["resource_storage"] == {}

    def test_map_count_two_district_holding_exactly_two_is_left_alone(self, test_db):
        """The real "outpost" shape: allow_multiple=False but map_count=2 —
        holding exactly 2 must not be touched at all (regression for the
        bug where the uniqueness gate ignored map_count and would have
        treated any 2nd instance as an illegal duplicate)."""
        test_db["district_defs"].insert_one({
            "key": "outpost", "display_name": "Outpost", "allow_multiple": False,
            "cost": {"wood": 4}, "map_count": 2,
        })
        old_nation = _ai_nation(districts=[
            {"_id": "d1", "def_key": "outpost", "node": "", "upgrades": []},
            {"_id": "d2", "def_key": "outpost", "node": "", "upgrades": []},
        ])
        new_nation = dict(old_nation)

        with _patched_mongo(test_db):
            result = th.ai_duplicate_building_cleanup_tick(old_nation, new_nation, {})

        assert result == ""
        assert {d["_id"] for d in new_nation["districts"]} == {"d1", "d2"}
        assert new_nation["resource_storage"] == {}

    def test_map_count_two_district_dismantles_only_the_excess(self, test_db):
        """3 outposts with map_count=2 must be reduced to 2, dismantling
        only the single excess instance — not collapsed down to 1."""
        test_db["district_defs"].insert_one({
            "key": "outpost", "display_name": "Outpost", "allow_multiple": False,
            "cost": {"wood": 4}, "map_count": 2,
        })
        old_nation = _ai_nation(districts=[
            {"_id": "d1", "def_key": "outpost", "node": "", "upgrades": []},
            {"_id": "d2", "def_key": "outpost", "node": "", "upgrades": []},
            {"_id": "d3", "def_key": "outpost", "node": "", "upgrades": []},
        ])
        new_nation = dict(old_nation)

        with _patched_mongo(test_db):
            th.ai_duplicate_building_cleanup_tick(old_nation, new_nation, {})

        remaining_ids = {d["_id"] for d in new_nation["districts"]}
        assert len(remaining_ids) == 2
        assert remaining_ids == {"d1", "d2"}
        # 1/4 of wood:4 = 1, dismantled once
        assert new_nation["resource_storage"] == {"wood": 1}

    def test_single_instance_is_never_touched(self, test_db):
        test_db["district_defs"].insert_one({
            "key": "forge", "display_name": "Forge", "allow_multiple": False, "cost": {"wood": 3},
        })
        old_nation = _ai_nation(districts=[
            {"_id": "d1", "def_key": "forge", "node": "", "upgrades": []},
        ])
        new_nation = dict(old_nation)

        with _patched_mongo(test_db):
            result = th.ai_duplicate_building_cleanup_tick(old_nation, new_nation, {})

        assert result == ""
        assert len(new_nation["districts"]) == 1

    def test_blank_placeholder_slots_are_ignored(self, test_db):
        old_nation = _ai_nation(districts=[
            {"_id": "d1"}, {"_id": "d2"},
        ])
        new_nation = dict(old_nation)

        with _patched_mongo(test_db):
            result = th.ai_duplicate_building_cleanup_tick(old_nation, new_nation, {})

        assert result == ""
        assert len(new_nation["districts"]) == 2

    def test_three_instances_keeps_only_one(self, test_db):
        test_db["district_defs"].insert_one({
            "key": "forge", "display_name": "Forge", "allow_multiple": False, "cost": {"wood": 4},
        })
        old_nation = _ai_nation(districts=[
            {"_id": "d1", "def_key": "forge", "node": "", "upgrades": []},
            {"_id": "d2", "def_key": "forge", "node": "", "upgrades": []},
            {"_id": "d3", "def_key": "forge", "node": "", "upgrades": []},
        ])
        new_nation = dict(old_nation)

        with _patched_mongo(test_db):
            th.ai_duplicate_building_cleanup_tick(old_nation, new_nation, {})

        assert {d["_id"] for d in new_nation["districts"]} == {"d1"}
        # 1/4 of wood:4 = 1, dismantled twice -> 2
        assert new_nation["resource_storage"] == {"wood": 2}

    def test_refund_is_capped_at_resource_capacity(self, test_db):
        test_db["district_defs"].insert_one({
            "key": "forge", "display_name": "Forge", "allow_multiple": False, "cost": {"wood": 20},
        })
        old_nation = _ai_nation(
            districts=[
                {"_id": "d1", "def_key": "forge", "node": "", "upgrades": []},
                {"_id": "d2", "def_key": "forge", "node": "", "upgrades": []},
            ],
            resource_storage={"wood": 9},
            nation_resource_capacity={"wood": 10},
        )
        new_nation = dict(old_nation)

        with _patched_mongo(test_db):
            th.ai_duplicate_building_cleanup_tick(old_nation, new_nation, {})

        # 1/4 of 20 = 5, but 9 + 5 = 14 is capped at 10
        assert new_nation["resource_storage"]["wood"] == 10

    def test_terrain_invalid_copy_is_preferred_for_dismantling(self, test_db):
        """A "coastal" district must be on land adjacent to water. If one
        copy is correctly coastal and the other is stranded inland, the
        inland one must be dismantled even if it comes first in the array."""
        test_db["district_defs"].insert_one({
            "key": "dock", "display_name": "Dock", "allow_multiple": False,
            "tile_requirement": "coastal", "cost": {"wood": 4},
        })
        old_nation = _ai_nation(districts=[
            {"_id": "d1", "def_key": "dock", "node": "", "upgrades": []},  # inland — wrong
            {"_id": "d2", "def_key": "dock", "node": "", "upgrades": []},  # coastal — correct
        ])
        new_nation = dict(old_nation)
        # d1: land tile with no water anywhere nearby.
        test_db["hex_map_tiles"].insert_one({
            "owner": "Test AI Nation", "q": 0, "r": 0, "terrain": "plains",
            "district": {"id": "d1", "def_key": "dock"},
        })
        test_db["hex_map_tiles"].insert_one({
            "owner": "Test AI Nation", "q": 1, "r": 0, "terrain": "plains",
        })
        # d2: land tile with an adjacent ocean tile.
        test_db["hex_map_tiles"].insert_one({
            "owner": "Test AI Nation", "q": 5, "r": 5, "terrain": "plains",
            "district": {"id": "d2", "def_key": "dock"},
        })
        test_db["hex_map_tiles"].insert_one({
            "owner": "Test AI Nation", "q": 6, "r": 5, "terrain": "ocean",
        })

        with patch("calculations.field_calculations.WATER_TERRAINS", {"ocean"}), \
             _patched_mongo(test_db):
            th.ai_duplicate_building_cleanup_tick(old_nation, new_nation, {})

        assert {d["_id"] for d in new_nation["districts"]} == {"d2"}

    def test_no_pending_tiles_writes_tile_immediately(self, test_db):
        test_db["district_defs"].insert_one({
            "key": "forge", "display_name": "Forge", "allow_multiple": False, "cost": {},
        })
        old_nation = _ai_nation(districts=[
            {"_id": "d1", "def_key": "forge", "node": "", "upgrades": []},
            {"_id": "d2", "def_key": "forge", "node": "", "upgrades": []},
        ])
        new_nation = dict(old_nation)
        test_db["hex_map_tiles"].insert_one({
            "owner": "Test AI Nation", "q": 0, "r": 0, "terrain": "plains",
            "district": {"id": "d1", "def_key": "forge"},
        })
        tile_id = test_db["hex_map_tiles"].insert_one({
            "owner": "Test AI Nation", "q": 2, "r": 2, "terrain": "plains",
            "district": {"id": "d2", "def_key": "forge"},
        }).inserted_id

        with _patched_mongo(test_db):
            th.ai_duplicate_building_cleanup_tick(old_nation, new_nation, {})

        tile = test_db["hex_map_tiles"].find_one({"_id": tile_id})
        assert tile["district"] is None


class TestDuplicateCities:
    def test_generic_city_duplicates_are_exempt(self, test_db):
        old_nation = _ai_nation(cities=[
            {"_id": "c1", "name": "", "type": "generic", "node": "", "wall": ""},
            {"_id": "c2", "name": "", "type": "generic", "node": "", "wall": ""},
        ])
        new_nation = dict(old_nation)

        with _patched_mongo(test_db):
            result = th.ai_duplicate_building_cleanup_tick(old_nation, new_nation, {})

        assert result == ""
        assert {c["_id"] for c in new_nation["cities"]} == {"c1", "c2"}

    def test_duplicate_non_generic_city_is_dismantled_and_refunded(self, test_db):
        old_nation = _ai_nation(cities=[
            {"_id": "c1", "name": "", "type": "heritage", "node": "", "wall": ""},
            {"_id": "c2", "name": "", "type": "heritage", "node": "", "wall": ""},
        ])
        new_nation = dict(old_nation)
        pending_tiles = []
        # Both placed on plain (non-capital, land) tiles so neither the
        # capital nor the terrain preference overrides array order here —
        # those get their own dedicated tests.
        test_db["hex_map_tiles"].insert_one({
            "owner": "Test AI Nation", "q": 2, "r": 2, "terrain": "plains",
            "city": {"id": "c1", "type": "heritage"},
        })
        test_db["hex_map_tiles"].insert_one({
            "owner": "Test AI Nation", "q": 3, "r": 3, "terrain": "plains",
            "city": {"id": "c2", "type": "heritage"},
        })

        with patch.object(th, "json_data", {"cities": {"heritage": {"cost": {"stone": 6, "wood": 2}}}}), \
             _patched_mongo(test_db):
            result = th.ai_duplicate_building_cleanup_tick(
                old_nation, new_nation, {}, pending_tiles=pending_tiles
            )

        assert {c["_id"] for c in new_nation["cities"]} == {"c1"}
        # 1/4 of {stone:6, wood:2} rounded up -> stone:2, wood:1
        assert new_nation["resource_storage"] == {"stone": 2, "wood": 1}
        assert "dismantled duplicate city" in result
        assert pending_tiles[0]["set"]["city"] is None

    def test_capital_flagged_city_is_kept_over_others(self, test_db):
        old_nation = _ai_nation(cities=[
            {"_id": "c1", "name": "", "type": "heritage", "node": "", "wall": ""},
            {"_id": "c2", "name": "", "type": "heritage", "node": "", "wall": ""},
        ])
        new_nation = dict(old_nation)
        test_db["hex_map_tiles"].insert_one({
            "owner": "Test AI Nation", "q": 4, "r": 4,
            "city": {"id": "c1", "type": "heritage"}, "capital": False,
        })
        test_db["hex_map_tiles"].insert_one({
            "owner": "Test AI Nation", "q": 5, "r": 5,
            "city": {"id": "c2", "type": "heritage"}, "capital": True,
        })

        with patch.object(th, "json_data", {"cities": {"heritage": {"cost": {}}}}), \
             _patched_mongo(test_db):
            th.ai_duplicate_building_cleanup_tick(old_nation, new_nation, {})

        # c2 is the capital — it must be the one kept, not c1.
        assert {c["_id"] for c in new_nation["cities"]} == {"c2"}

    def test_city_on_water_is_preferred_for_dismantling_over_one_on_land(self, test_db):
        """Cities have no per-type terrain requirement, but are always
        land-only — a copy sitting on water (a corrupted state) must lose
        out to a properly land-placed copy, with no capital involved."""
        old_nation = _ai_nation(cities=[
            {"_id": "c1", "name": "", "type": "heritage", "node": "", "wall": ""},  # on water — wrong
            {"_id": "c2", "name": "", "type": "heritage", "node": "", "wall": ""},  # on land — correct
        ])
        new_nation = dict(old_nation)
        test_db["hex_map_tiles"].insert_one({
            "owner": "Test AI Nation", "q": 6, "r": 6, "terrain": "ocean",
            "city": {"id": "c1", "type": "heritage"},
        })
        test_db["hex_map_tiles"].insert_one({
            "owner": "Test AI Nation", "q": 7, "r": 7, "terrain": "plains",
            "city": {"id": "c2", "type": "heritage"},
        })

        with patch("calculations.field_calculations.WATER_TERRAINS", {"ocean"}), \
             patch.object(th, "json_data", {"cities": {"heritage": {"cost": {}}}}), \
             _patched_mongo(test_db):
            th.ai_duplicate_building_cleanup_tick(old_nation, new_nation, {})

        assert {c["_id"] for c in new_nation["cities"]} == {"c2"}

        with patch.object(th, "json_data", {"cities": {"heritage": {"cost": {}}}}), \
             _patched_mongo(test_db):
            th.ai_duplicate_building_cleanup_tick(old_nation, new_nation, {})

        # c2 is the capital — it must be the one kept, not c1.
        assert {c["_id"] for c in new_nation["cities"]} == {"c2"}
