"""
Regression test for a real bug found while adding the minimum city-distance
rule to _select_best_city (helpers/ai_decision_helpers.py): its "has_city"
check read old_nation["cities"], which never changes across the several
_select_best_city calls evaluate_goal_district can make in a single tick
(only new_nation["cities"] accumulates as cities are built). So a nation
building a SECOND city in the same call was wrongly treated as building its
very FIRST city — forced onto the capital branch again — silently
overwriting the first city's claim on that tile instead of choosing a new
one via the normal scored/distance-filtered path.

Fixed with an explicit already_has_a_city_this_call flag, set by
evaluate_goal_district once cities_built_this_call > 0, rather than
inferring it from old_nation.
"""
from bson import ObjectId

import helpers.ai_decision_helpers as adh


def _base_state():
    return {
        "net_production": {}, "stockpiles": {}, "money_income": 0,
        "money": 1000, "active_resources": set(),
    }


def _tiles(owner, q_range, r=0, capital_at=None):
    tiles = []
    for q in q_range:
        t = {"q": q, "r": r, "terrain": "plains", "owner": owner}
        if capital_at and (q, r) == capital_at:
            t["capital"] = True
        tiles.append(t)
    return tiles


class TestAlreadyHasACityThisCallOverride:
    def test_without_override_a_second_call_still_forces_the_capital(self):
        """Pins the buggy-if-unfixed behavior at the _select_best_city
        level: with no override and old_nation["cities"] still empty (as it
        would be mid-call), the capital gets reused."""
        nation = {
            "_id": ObjectId(), "name": "Alpha", "city_slots": 2, "cities": [],
            "government_type": "Fallen Monarchy",
        }
        owned_tiles = _tiles("Alpha", range(0, 10), capital_at=(0, 0))

        plan = adh._select_best_city(nation, _base_state(), owned_tiles=owned_tiles, world_city_coords=set())

        assert plan and plan.get("placement") == {"q": 0, "r": 0}

    def test_with_override_the_second_city_goes_through_scored_placement(self):
        """Same setup, but with already_has_a_city_this_call=True (as
        evaluate_goal_district now passes once cities_built_this_call > 0)
        — the capital-forced branch must NOT fire, and the chosen tile must
        respect the distance rule against the (now-occupied) capital."""
        nation = {
            "_id": ObjectId(), "name": "Alpha", "city_slots": 2, "cities": [],
            "government_type": "Fallen Monarchy",
        }
        owned_tiles = _tiles("Alpha", range(0, 10), capital_at=(0, 0))
        world_city_coords = {(0, 0)}  # the capital already has a city on it now

        plan = adh._select_best_city(
            nation, _base_state(), owned_tiles=owned_tiles, world_city_coords=world_city_coords,
            already_has_a_city_this_call=True,
        )

        assert plan and plan.get("placement")
        assert plan["placement"] != {"q": 0, "r": 0}
        from helpers.hex_map_helpers import hex_distance
        assert hex_distance(plan["placement"]["q"], plan["placement"]["r"], 0, 0) >= adh.MIN_CITY_TILE_DISTANCE
