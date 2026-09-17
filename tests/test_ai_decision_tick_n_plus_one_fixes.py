"""
Regression tests for two per-nation N+1/round-trip fixes made while
investigating a real production incident (2026-09-17): AI Decision Tick had
processed only 15 of 216 nations in 32+ minutes with zero errors, zero
retries, and zero memory pressure — a genuine "death by a thousand cuts"
per-nation query-count problem, not a hang.

1. get_stored_market_prices issued one mongo.db.markets.find_one() per
   market linked to the nation. Replaced with a single batched
   mongo.db.markets.find({"_id": {"$in": [...]}}) query.

2. evaluate_goal_district issued two separate per-nation hex_map_tiles
   queries: one for owned_tiles_snapshot ({"owner": nation_name}, full
   projection) and a second, redundant one for map_claimed_counts
   ({"owner": nation_name, "district": {"$exists": True, "$ne": None}}) —
   data already fully present in owned_tiles_snapshot's own "district"
   field. The second query is now derived from owned_tiles_snapshot in
   memory instead of hitting the database again. owned_tiles_snapshot
   itself was also switched from a direct hex_map_tiles.find() to reading
   through get_all_tiles_from_chunks() (the chunk-cache mirror, process-wide
   cached), so repeated calls across many nations in the same tick reuse one
   cached read instead of issuing 216 separate full round trips.
"""
from unittest.mock import MagicMock, patch
from bson import ObjectId

import helpers.ai_decision_helpers as adh
import helpers.hex_map_helpers as hmh
from app_core import category_data


class TestGetStoredMarketPricesIsBatched:
    def test_uses_one_batched_find_not_one_find_one_per_market(self, test_db):
        nation_id = ObjectId()
        old_nation = {"_id": nation_id}

        market_ids = [ObjectId() for _ in range(3)]
        test_db["market_links"].insert_many([
            {"member": str(nation_id), "market": str(mid)} for mid in market_ids
        ])
        test_db["markets"].insert_many([
            {"_id": mid, "resource_prices": {"wood": 5 + i}} for i, mid in enumerate(market_ids)
        ])

        original_market_links_db = category_data["market_links"]["database"]
        category_data["market_links"]["database"] = test_db["market_links"]
        fake_mongo = type("FakeMongo", (), {"db": test_db})()
        try:
            with patch.object(adh, "mongo", fake_mongo), \
                 patch.object(test_db["markets"], "find_one", wraps=test_db["markets"].find_one) as find_one_spy:
                prices = adh.get_stored_market_prices(old_nation)
        finally:
            category_data["market_links"]["database"] = original_market_links_db

        assert find_one_spy.call_count == 0, "expected no per-market find_one calls"
        assert prices["wood"] == 7  # highest across the 3 linked markets (5, 6, 7)

    def test_falls_back_to_base_prices_for_unlinked_resources(self, test_db):
        nation_id = ObjectId()
        old_nation = {"_id": nation_id}
        test_db["market_links"].insert_one({"member": str(nation_id), "market": str(ObjectId())})

        original_market_links_db = category_data["market_links"]["database"]
        category_data["market_links"]["database"] = test_db["market_links"]
        fake_mongo = type("FakeMongo", (), {"db": test_db})()
        try:
            with patch.object(adh, "mongo", fake_mongo):
                prices = adh.get_stored_market_prices(old_nation)
        finally:
            category_data["market_links"]["database"] = original_market_links_db

        base = adh._base_prices()
        assert prices == {r: float(p) for r, p in base.items()}

    def test_no_linked_markets_returns_base_prices_without_querying_markets(self, test_db):
        old_nation = {"_id": ObjectId()}

        original_market_links_db = category_data["market_links"]["database"]
        category_data["market_links"]["database"] = test_db["market_links"]
        fake_mongo = MagicMock()
        try:
            with patch.object(adh, "mongo", fake_mongo):
                prices = adh.get_stored_market_prices(old_nation)
        finally:
            category_data["market_links"]["database"] = original_market_links_db

        fake_mongo.db.markets.find.assert_not_called()
        base = adh._base_prices()
        assert prices == {r: float(p) for r, p in base.items()}


def _base_state(district_slots=1):
    return {
        "money": 1000, "money_income": 0, "stockpiles": {}, "net_production": {},
        "resource_capacity": {}, "active_resources": set(), "open_district_slots": district_slots,
        "existing_def_keys": set(), "existing_def_key_counts": {}, "available_jobs": {},
    }


class TestEvaluateGoalDistrictMapClaimedCountsNoSecondQuery:
    def _run(self, test_db):
        nation_name = "Test Nation"
        old_nation = {
            "_id": ObjectId(), "name": nation_name, "money": 1000,
            "resource_storage": {}, "cities": [], "districts": [],
            "government_type": "Standard",
        }
        new_nation = dict(old_nation)
        state = _base_state()
        goal = {"type": "expand_economy", "display_name": "Expand Economy", "score": 1}

        test_db["hex_map_tiles"].insert_many([
            {"q": 1, "r": 1, "owner": nation_name, "terrain": "plains",
             "district": {"id": "d1", "def_key": "farm", "display_name": "Farm", "type": ""}},
            {"q": 2, "r": 1, "owner": nation_name, "terrain": "plains",
             "district": {"id": "d2", "def_key": "farm", "display_name": "Farm", "type": ""}},
            {"q": 3, "r": 1, "owner": nation_name, "terrain": "plains"},  # no district
        ])

        candidate = (10.0, "farm", "Farm", {"money": 50}, "test rationale", "db")
        dd = {"key": "farm", "display_name": "Farm", "tile_requirement": "land", "modifiers": [], "map_count": 5}
        test_db["district_defs"].insert_one(dd)

        fake_mongo = type("FakeMongo", (), {"db": test_db})()
        with patch.object(adh, "mongo", fake_mongo), \
             patch.object(hmh, "mongo", fake_mongo), \
             patch.object(test_db["hex_map_tiles"], "find", wraps=test_db["hex_map_tiles"].find) as find_spy, \
             patch.object(adh, "_select_best_city", return_value=None), \
             patch.object(adh, "score_buildable_districts", return_value=[candidate]), \
             patch.object(adh, "_apply_goal_alignment", return_value=([candidate], set(), set(), set())), \
             patch.object(adh, "get_ai_personality", return_value={}), \
             patch.object(adh, "_nation_is_nomadic", return_value=False), \
             patch.object(adh, "compute_upkeep_floor", return_value=({}, {}, {}, {}, 1.0, {})), \
             patch.object(adh, "select_strategic_goal", return_value=(goal, [])):
            result = adh.evaluate_goal_district(
                old_nation, new_nation, state, goal, {}, {}, {}, [], dry_run=False, pending_tiles=[],
                world_city_coords=set(),
            )
        return result, find_spy

    def test_reads_owned_tiles_exactly_once(self, test_db):
        """map_claimed_counts must no longer issue its own separate
        hex_map_tiles.find({"owner": ..., "district": {...}}) query — the
        only find() calls left should be the chunk-cache self-heal rebuild
        (an unfiltered {} scan) and _claim_district_tile's per-coordinate
        write-path lookup, neither of which filter on "district"."""
        (district_plan, _, district_log, _, _), find_spy = self._run(test_db)

        district_filter_calls = [
            c for c in find_spy.call_args_list
            if isinstance(c.args[0], dict) and "district" in c.args[0]
        ]
        assert not district_filter_calls, (
            f"map_claimed_counts should be derived from owned_tiles_snapshot in memory, "
            f"not a second DB query: {district_filter_calls}"
        )

    def test_existing_map_count_is_still_correctly_enforced(self, test_db):
        """map_claimed_counts derived from owned_tiles_snapshot must still
        correctly reflect the 2 pre-existing 'farm' tiles: with map_count=5
        and 2 already claimed, a 3rd farm is still legal to build."""
        (district_plan, _, district_log, _, _), _ = self._run(test_db)

        assert any("Built district" in line for line in district_log), district_log


class TestEvaluateGoalDistrictReadsThroughChunkCache:
    def test_owned_tiles_snapshot_reads_via_chunk_cache_not_a_direct_scan(self, test_db):
        """owned_tiles_snapshot must come from get_all_tiles_from_chunks
        (shared, process-cached across every nation in the tick) instead of
        a fresh per-nation hex_map_tiles.find({"owner": ...})."""
        nation_name = "Test Nation"
        old_nation = {
            "_id": ObjectId(), "name": nation_name, "money": 1000,
            "resource_storage": {}, "cities": [], "districts": [],
            "government_type": "Standard",
        }
        new_nation = dict(old_nation)
        state = _base_state()
        goal = {"type": "expand_economy", "display_name": "Expand Economy", "score": 1}
        test_db["hex_map_tiles"].insert_one(
            {"q": 1, "r": 1, "owner": nation_name, "terrain": "plains"}
        )

        fake_mongo = type("FakeMongo", (), {"db": test_db})()
        with patch.object(adh, "mongo", fake_mongo), \
             patch.object(hmh, "mongo", fake_mongo), \
             patch.object(adh, "get_all_tiles_from_chunks", wraps=hmh.get_all_tiles_from_chunks) as chunks_spy, \
             patch.object(adh, "_select_best_city", return_value=None), \
             patch.object(adh, "score_buildable_districts", return_value=[]), \
             patch.object(adh, "get_ai_personality", return_value={}), \
             patch.object(adh, "_nation_is_nomadic", return_value=False), \
             patch.object(adh, "compute_upkeep_floor", return_value=({}, {}, {}, {}, 1.0, {})), \
             patch.object(adh, "select_strategic_goal", return_value=(goal, [])):
            adh.evaluate_goal_district(
                old_nation, new_nation, state, goal, {}, {}, {}, [], dry_run=False, pending_tiles=[],
                world_city_coords=set(),
            )

        chunks_spy.assert_called_once()

    def test_nomadic_nation_never_reads_tiles_at_all(self, test_db):
        old_nation = {
            "_id": ObjectId(), "name": "Nomad Nation", "money": 1000,
            "resource_storage": {}, "cities": [], "districts": [],
            "government_type": "Nomadic Horde",
        }
        new_nation = dict(old_nation)
        state = _base_state()
        goal = {"type": "expand_economy", "display_name": "Expand Economy", "score": 1}

        fake_mongo = type("FakeMongo", (), {"db": test_db})()
        with patch.object(adh, "mongo", fake_mongo), \
             patch.object(hmh, "mongo", fake_mongo), \
             patch.object(adh, "get_all_tiles_from_chunks", wraps=hmh.get_all_tiles_from_chunks) as chunks_spy, \
             patch.object(adh, "_select_best_city", return_value=None), \
             patch.object(adh, "score_buildable_districts", return_value=[]), \
             patch.object(adh, "get_ai_personality", return_value={}), \
             patch.object(adh, "_nation_is_nomadic", return_value=True), \
             patch.object(adh, "compute_upkeep_floor", return_value=({}, {}, {}, {}, 1.0, {})), \
             patch.object(adh, "select_strategic_goal", return_value=(goal, [])):
            adh.evaluate_goal_district(
                old_nation, new_nation, state, goal, {}, {}, {}, [], dry_run=False, pending_tiles=[],
                world_city_coords=set(),
            )

        chunks_spy.assert_not_called()
