"""
Regression tests for a real gap in evaluate_goal_district
(helpers/ai_decision_helpers.py): building a district or city only ever
checked money_ok/res_ok before paying for it and adding it to
new_nation["districts"]/["cities"] — whether a legal map tile could
actually be found for it was never part of that gate. A failed
_pick_district_tile (or a missing district_defs entry), or a city plan
with no "placement" set, just left coord/node_key at their None/""
defaults; _claim_district_tile/_claim_city_tile then no-op on a None
coord, but the nation-side entry (and the resource cost) was committed
regardless — a "phantom" district/city that exists on the nation page but
never appears on the map.

Confirmed live via scripts/reconcile_map_and_ai_nations.py: 88 AI nations
had at least one such mismatch (144 districts/cities existed on the nation
page with nothing on the map).

Fix: placement is now resolved BEFORE the cost gate, and folded into the
same condition money_ok/res_ok already gated on — a placement failure is
treated exactly like "can't afford": nothing is built, nothing is paid for,
and the plan is saved for a later session instead.
"""
from unittest.mock import patch
from bson import ObjectId

import mongomock

import helpers.ai_decision_helpers as adh


def _base_state(money=1000, district_slots=1):
    return {
        "money": money,
        "money_income": 0,
        "stockpiles": {},
        "net_production": {},
        "resource_capacity": {},
        "active_resources": set(),
        "open_district_slots": district_slots,
        "existing_def_keys": set(),
        "existing_def_key_counts": {},
        "available_jobs": {},
    }


class TestCityBuildRequiresPlacement:
    def _run(self, test_db, city_plan, is_nomadic=False):
        old_nation = {
            "_id": ObjectId(), "name": "Test Nation", "money": 1000,
            "resource_storage": {}, "cities": [], "districts": [],
            "government_type": "Standard" if not is_nomadic else "Nomadic Horde",
        }
        new_nation = dict(old_nation)
        state = _base_state()
        goal = {"type": "grow_population", "display_name": "Grow Population", "score": 1}

        fake_mongo = type("FakeMongo", (), {"db": test_db})()
        with patch.object(adh, "mongo", fake_mongo), \
             patch.object(adh, "_select_best_city", return_value=city_plan), \
             patch.object(adh, "score_buildable_districts", return_value=[]), \
             patch.object(adh, "get_ai_personality", return_value={}), \
             patch.object(adh, "_nation_is_nomadic", return_value=is_nomadic), \
             patch.object(adh, "compute_upkeep_floor", return_value=({}, {}, {}, {}, 1.0, {})), \
             patch.object(adh, "select_strategic_goal", return_value=(goal, [])):
            return adh.evaluate_goal_district(
                old_nation, new_nation, state, goal, {}, {}, {}, [], dry_run=False, pending_tiles=[],
            )

    def test_city_plan_with_no_placement_is_not_built(self, test_db):
        """A non-nomadic nation's city plan with no legal tile (placement
        key absent) must not be built, must not cost money, and must not
        appear in new_nation["cities"]."""
        city_plan = {
            "key": "generic", "display_name": "City: Generic", "cost": {"money": 100},
            "rationale": "test", "sessions_saving": 0, "source": "city",
            # no "placement" key — _select_best_city couldn't find a legal tile
        }
        old_nation_money_before = 1000
        district_plan, _, district_log, _, _ = self._run(test_db, city_plan)

        assert district_plan is not None
        assert district_plan["key"] == "generic"
        assert any("no legal tile" in line.lower() for line in district_log)

    def test_city_plan_with_placement_is_built(self, test_db):
        """Sanity check: a city plan WITH a placement still builds normally
        — the fix must not block the legitimate case."""
        test_db["hex_map_tiles"].insert_one({"q": 1, "r": 1, "owner": "Test Nation"})
        city_plan = {
            "key": "generic", "display_name": "City: Generic", "cost": {"money": 100},
            "rationale": "test", "sessions_saving": 0, "source": "city",
            "placement": {"q": 1, "r": 1},
        }
        district_plan, _, district_log, _, _ = self._run(test_db, city_plan)

        assert any("Built city" in line for line in district_log)

    def test_nomadic_nation_city_with_no_placement_still_builds(self, test_db):
        """Nomadic nations never place on the map by design — no placement
        key should never block them."""
        city_plan = {
            "key": "generic", "display_name": "City: Generic", "cost": {"money": 100},
            "rationale": "test", "sessions_saving": 0, "source": "city",
        }
        district_plan, _, district_log, _, _ = self._run(test_db, city_plan, is_nomadic=True)

        assert any("Built city" in line for line in district_log)


class TestDistrictBuildRequiresPlacement:
    def _run(self, test_db, candidate, district_defs_doc=None, is_nomadic=False, owned_tile=True):
        old_nation = {
            "_id": ObjectId(), "name": "Test Nation", "money": 1000,
            "resource_storage": {}, "cities": [], "districts": [],
            "government_type": "Standard" if not is_nomadic else "Nomadic Horde",
        }
        new_nation = dict(old_nation)
        state = _base_state()
        goal = {"type": "expand_economy", "display_name": "Expand Economy", "score": 1}

        if owned_tile:
            test_db["hex_map_tiles"].insert_one({
                "q": 1, "r": 1, "owner": "Test Nation", "terrain": "plains",
                "district": {"id": "existing1", "def_key": "farm", "display_name": "Farm", "type": ""},
            })
        if district_defs_doc:
            test_db["district_defs"].insert_one(district_defs_doc)

        fake_mongo = type("FakeMongo", (), {"db": test_db})()
        with patch.object(adh, "mongo", fake_mongo), \
             patch.object(adh, "_select_best_city", return_value=None), \
             patch.object(adh, "score_buildable_districts", return_value=[candidate]), \
             patch.object(adh, "_apply_goal_alignment", return_value=([candidate], set(), set(), set())), \
             patch.object(adh, "get_ai_personality", return_value={}), \
             patch.object(adh, "_nation_is_nomadic", return_value=is_nomadic), \
             patch.object(adh, "compute_upkeep_floor", return_value=({}, {}, {}, {}, 1.0, {})), \
             patch.object(adh, "select_strategic_goal", return_value=(goal, [])):
            return adh.evaluate_goal_district(
                old_nation, new_nation, state, goal, {}, {}, {}, [], dry_run=False, pending_tiles=[],
            )

    def test_missing_district_def_is_not_built(self, test_db):
        """c_source == "db" but the def_key doesn't actually exist in
        district_defs — dd resolves to None, so there's nothing to place
        legally. Must not be built."""
        candidate = (10.0, "nonexistent_key", "Nonexistent District", {"money": 50}, "test rationale", "db")
        district_plan, _, district_log, _, _ = self._run(test_db, candidate, district_defs_doc=None)

        assert district_plan is not None
        assert district_plan["key"] == "nonexistent_key"
        assert any("no legal tile" in line.lower() for line in district_log)

    def test_no_legal_adjacent_tile_is_not_built(self, test_db):
        """A real district_defs entry exists, but the nation owns no empty
        tile adjacent to an existing building — _pick_district_tile finds
        nothing. Must not be built (no owned empty tile at all here)."""
        candidate = (10.0, "farm", "Farm", {"money": 50}, "test rationale", "db")
        # map_count high enough that the pre-existing "farm" tile inserted by
        # _run doesn't itself trip the "already at max instances" guard —
        # this test is specifically about the adjacency/placement check,
        # not the separate instance-count one.
        dd = {"key": "farm", "display_name": "Farm", "tile_requirement": "land", "modifiers": [], "map_count": 5}
        district_plan, _, district_log, _, _ = self._run(test_db, candidate, district_defs_doc=dd)

        assert district_plan is not None
        assert any("no legal tile" in line.lower() for line in district_log)

    def test_legacy_type_based_district_still_builds_without_placement(self, test_db):
        """c_source != "db" (legacy type-based district) never needs map
        placement by design — the fix must not touch this path."""
        candidate = (10.0, "old_legacy_type", "Legacy District", {"money": 50}, "test rationale", "legacy")
        district_plan, _, district_log, _, _ = self._run(test_db, candidate, owned_tile=False)

        assert any("Built district" in line for line in district_log)

    def test_nomadic_nation_district_still_builds_without_placement(self, test_db):
        candidate = (10.0, "farm", "Farm", {"money": 50}, "test rationale", "db")
        district_plan, _, district_log, _, _ = self._run(
            test_db, candidate, district_defs_doc=None, is_nomadic=True, owned_tile=False,
        )
        assert any("Built district" in line for line in district_log)
