"""
Regression tests for the FORWARD-LOOKING half of the minimum city-distance
rule (see tests/test_fix_city_placement.py for the retroactive-correction
admin tool): every place that chooses a brand-new city tile —
_select_best_city (the live AI tick's city-building decision),
sync_nation_cities's Nation -> Map placement path, and the tick dispatcher
that feeds a world-wide city-coordinate cache into both without an N+1
query per nation — must also respect
ai_decision_helpers.MIN_CITY_TILE_DISTANCE.
"""
from unittest.mock import patch
from bson import ObjectId

import helpers.ai_decision_helpers as adh
import helpers.hex_map_helpers as hmh
from helpers.hex_map_helpers import hex_distance

MIN_DIST = adh.MIN_CITY_TILE_DISTANCE


def _patched_mongo(test_db):
    fake_mongo = type("FakeMongo", (), {"db": test_db})()
    return patch.object(adh, "mongo", fake_mongo)


def _line_tiles(owner, q_range, r=0, city_at=None, capital_at=None):
    """Every (q, r) for q in q_range, owned by `owner`, all empty except
    `city_at` (a (q, r) tuple, carries a city object) and/or `capital_at`
    (flagged capital=True, no city). _compute_legal_placement needs at
    least one of these as an anchor — a nation with zero buildings AND no
    capital tile returns an empty legal_city_tiles unconditionally."""
    tiles = []
    for q in q_range:
        t = {"q": q, "r": r, "terrain": "plains", "owner": owner}
        if city_at and (q, r) == city_at:
            t["city"] = {"id": "existing", "type": "generic", "name": ""}
        if capital_at and (q, r) == capital_at:
            t["capital"] = True
        tiles.append(t)
    return tiles


def _base_ai_state():
    return {
        "net_production": {}, "stockpiles": {}, "money_income": 0,
        "money": 1000, "active_resources": set(),
    }


class TestSelectBestCityRespectsWorldCityCoords:
    def test_new_city_stays_at_least_min_distance_from_every_world_city(self):
        nation = {
            "_id": ObjectId(), "name": "Alpha", "city_slots": 2,
            "cities": [{"_id": "existing", "type": "generic", "name": "", "node": "", "wall": ""}],
            "government_type": "Fallen Monarchy",
        }
        owned_tiles = _line_tiles("Alpha", range(-1, 9), city_at=(0, 0))
        # (0,0) is Alpha's own city; (2,0) belongs to some other nation not
        # represented in owned_tiles at all — world_city_coords is a plain
        # coordinate set, independent of ownership.
        world_city_coords = {(0, 0), (2, 0)}

        plan = adh._select_best_city(
            nation, _base_ai_state(), owned_tiles=owned_tiles, world_city_coords=world_city_coords,
        )

        assert plan is not None and plan.get("placement"), f"expected a placement, got {plan}"
        coord = (plan["placement"]["q"], plan["placement"]["r"])
        for other in world_city_coords:
            assert hex_distance(coord[0], coord[1], other[0], other[1]) >= MIN_DIST, (
                f"chosen tile {coord} is too close to existing city at {other}"
            )

    def test_without_any_world_cities_a_close_tile_is_allowed(self):
        """Sanity/regression guard: the filter must not be permanently
        excluding nearby tiles — only ones actually close to a real entry
        in world_city_coords."""
        nation = {
            "_id": ObjectId(), "name": "Alpha", "city_slots": 2,
            "cities": [{"_id": "existing", "type": "generic", "name": "", "node": "", "wall": ""}],
            "government_type": "Fallen Monarchy",
        }
        # Only two owned tiles at all: the nation's own existing city, and
        # one immediately adjacent (distance 1) — the only possible pick.
        owned_tiles = _line_tiles("Alpha", range(0, 2), city_at=(0, 0))

        plan = adh._select_best_city(
            nation, _base_ai_state(), owned_tiles=owned_tiles, world_city_coords=set(),
        )

        assert plan is not None and plan.get("placement")
        assert (plan["placement"]["q"], plan["placement"]["r"]) == (1, 0)

    def test_world_city_coords_self_fetches_from_the_database_when_omitted(self, test_db):
        nation = {
            "_id": ObjectId(), "name": "Alpha", "city_slots": 2,
            "cities": [{"_id": "existing", "type": "generic", "name": "", "node": "", "wall": ""}],
            "government_type": "Fallen Monarchy",
        }
        owned_tiles = _line_tiles("Alpha", range(-1, 9), city_at=(0, 0))
        # A city belonging to a totally different nation, on a tile not even
        # part of Alpha's owned_tiles — only discoverable via the DB self-fetch.
        test_db["hex_map_tiles"].insert_one({
            "q": 2, "r": 0, "terrain": "plains", "owner": "Beta",
            "city": {"id": "beta_city", "type": "generic", "name": ""},
        })

        with _patched_mongo(test_db):
            plan = adh._select_best_city(nation, _base_ai_state(), owned_tiles=owned_tiles)

        assert plan is not None and plan.get("placement")
        coord = (plan["placement"]["q"], plan["placement"]["r"])
        assert hex_distance(coord[0], coord[1], 2, 0) >= MIN_DIST


class TestSyncNationCitiesRespectsWorldCityCoords:
    def test_placement_avoids_another_nations_city(self):
        # An existing city at (-5,0) anchors _compute_legal_placement (a
        # nation with zero buildings and no capital tile can't place a city
        # at all — see _line_tiles' docstring) and makes has_city_on_map
        # True, so the new city goes through the scored (not capital-forced)
        # branch, which is the one the distance filter actually applies to.
        nation = {"_id": ObjectId(), "name": "Alpha", "cities": [
            {"_id": "existing", "type": "generic", "name": "", "node": "", "wall": ""},
            {"_id": "new_city", "type": "generic", "name": "", "node": "", "wall": ""},
        ]}
        owned_tiles = _line_tiles("Alpha", range(-5, 9), city_at=(-5, 0))
        tiles_with_city = [t for t in owned_tiles if t.get("city")]
        world_city_coords = {(-5, 0), (2, 0)}  # some other nation's cities

        original_conflicts = set(world_city_coords)  # sync_nation_cities adds the chosen coord in place
        report = adh.sync_nation_cities(
            nation, dry_run=True, tiles_with_city=tiles_with_city, owned_tiles=owned_tiles,
            world_city_coords=world_city_coords,
        )

        assert len(report["placed_on_map"]) == 1
        coord = tuple(report["placed_on_map"][0]["coord"])
        for other in original_conflicts:
            assert hex_distance(coord[0], coord[1], other[0], other[1]) >= MIN_DIST

    def test_world_city_coords_self_fetches_when_omitted(self, test_db):
        nation = {"_id": ObjectId(), "name": "Alpha", "cities": [
            {"_id": "existing", "type": "generic", "name": "", "node": "", "wall": ""},
            {"_id": "new_city", "type": "generic", "name": "", "node": "", "wall": ""},
        ]}
        owned_tiles = _line_tiles("Alpha", range(-5, 9), city_at=(-5, 0))
        tiles_with_city = [t for t in owned_tiles if t.get("city")]
        test_db["hex_map_tiles"].insert_one({
            "q": 2, "r": 0, "terrain": "plains", "owner": "Beta",
            "city": {"id": "beta_city", "type": "generic", "name": ""},
        })

        with _patched_mongo(test_db):
            report = adh.sync_nation_cities(
                nation, dry_run=True, tiles_with_city=tiles_with_city, owned_tiles=owned_tiles,
            )

        assert len(report["placed_on_map"]) == 1
        coord = tuple(report["placed_on_map"][0]["coord"])
        assert hex_distance(coord[0], coord[1], 2, 0) >= MIN_DIST

    def test_second_city_in_same_batch_avoids_the_first(self):
        """Two blank cities filled in the same sync_nation_cities call must
        not land on top of (or too close to) each other — reserved_coords/
        world_city_coords are both consulted for every subsequent pick in
        the same pass."""
        nation = {"_id": ObjectId(), "name": "Alpha", "cities": [
            {"_id": "existing", "type": "generic", "name": "", "node": "", "wall": ""},
            {"_id": "city_1", "type": "generic", "name": "", "node": "", "wall": ""},
            {"_id": "city_2", "type": "generic", "name": "", "node": "", "wall": ""},
        ]}
        owned_tiles = _line_tiles("Alpha", range(-5, 9), city_at=(-5, 0))
        tiles_with_city = [t for t in owned_tiles if t.get("city")]

        report = adh.sync_nation_cities(
            nation, dry_run=True, tiles_with_city=tiles_with_city, owned_tiles=owned_tiles,
            world_city_coords=set(),
        )

        assert len(report["placed_on_map"]) == 2
        c1 = tuple(report["placed_on_map"][0]["coord"])
        c2 = tuple(report["placed_on_map"][1]["coord"])
        assert hex_distance(c1[0], c1[1], c2[0], c2[1]) >= MIN_DIST


class TestEvaluateGoalDistrictSharesWorldCityCoordsAcrossBuilds:
    def test_two_cities_built_in_one_call_stay_apart_and_the_set_grows(self, test_db):
        """A nation building 2 cities in the same evaluate_goal_district call
        (both real _select_best_city picks, not mocked) must not place the
        second one too close to the first — and the caller-supplied
        world_city_coords set must reflect both by the end of the call, so a
        different nation processed right after in the same tick sees them."""
        # A generous stockpile of every general/unique resource, not just the
        # cheapest city type's costs — the real scorer may pick whichever
        # city type has the highest effective_pop_capacity (e.g. "Heritage",
        # which costs iron), and this test cares about placement/distance
        # behavior, not which specific city type wins.
        big_stockpile = {r: 1000 for r in ["wood", "stone", "food", "mounts", "research", "magic", "iron", "gunpowder"]}
        nation_id = ObjectId()
        old_nation = {
            "_id": nation_id, "name": "Alpha", "money": 1000, "city_slots": 2,
            "cities": [], "districts": [],
            "resource_storage": dict(big_stockpile),
            "government_type": "Fallen Monarchy",
        }
        new_nation = dict(old_nation)
        state = {
            "money": 1000, "money_income": 0,
            "stockpiles": dict(big_stockpile),
            "net_production": {}, "resource_capacity": {}, "active_resources": set(),
            "open_district_slots": 0, "existing_def_keys": set(),
            "existing_def_key_counts": {}, "available_jobs": {},
        }
        goal = {"type": "grow_population", "display_name": "Grow Population", "score": 1}
        world_city_coords = set()

        # A capital-flagged (but cityless) tile anchors the first build —
        # a nation with zero buildings and no capital can't legally place
        # a city at all (see _line_tiles' docstring). The first city is
        # forced onto the capital regardless of world_city_coords (a fixed
        # administrative point, not a new choice); the second goes through
        # the real scored/distance-filtered path.
        for t in _line_tiles("Alpha", range(0, 20), capital_at=(0, 0)):
            test_db["hex_map_tiles"].insert_one(t)

        fake_mongo = type("FakeMongo", (), {"db": test_db})()
        with patch.object(adh, "mongo", fake_mongo), \
             patch.object(hmh, "mongo", fake_mongo), \
             patch.object(adh, "score_buildable_districts", return_value=[]), \
             patch.object(adh, "get_ai_personality", return_value={}), \
             patch.object(adh, "_nation_is_nomadic", return_value=False), \
             patch.object(adh, "compute_upkeep_floor", return_value=({}, {}, {}, {}, 1.0, {})), \
             patch.object(adh, "select_strategic_goal", return_value=(goal, [])):
            district_plan, _, district_log, _, _ = adh.evaluate_goal_district(
                old_nation, new_nation, state, goal, {}, {}, {}, [], dry_run=True,
                pending_tiles=[], world_city_coords=world_city_coords,
            )

        built_lines = [l for l in district_log if l.startswith("Built city:")]
        assert len(built_lines) == 2, f"expected 2 cities built, got: {district_log}"
        assert len(world_city_coords) == 2

        coords = list(world_city_coords)
        assert hex_distance(coords[0][0], coords[0][1], coords[1][0], coords[1][1]) >= MIN_DIST
