"""
Tests for _update_tech_costs, specifically the minimum-cost floor.

Bug: the floor used base_cost // 2 (truncating), while every other floor
check in the codebase (_validate_tech_costs, nation_tech_cost_reduction_tick,
forms.py) uses (base_cost + 1) // 2 (ceiling). For odd base costs this let
_update_tech_costs — which runs unconditionally on every nation change
approval via _calculate_and_attach_fields — recompute and re-persist a cost
one below the real floor, so a manual fix bringing a tech cost back up to the
correct floor would immediately get recomputed back down to the wrong value
on the very next approval.
"""
from unittest.mock import patch

from helpers.change_helpers import _update_tech_costs

_TECH_JSON = {
    "odd_cost_tech": {"cost": 7, "type": "Culture"},
    "even_cost_tech": {"cost": 6, "type": "Culture"},
    "one_cost_tech": {"cost": 3, "type": "Military"},
}


def _patched():
    return patch("helpers.change_helpers.json_data", {"tech": _TECH_JSON})


class TestUpdateTechCostsFloor:
    def test_odd_base_cost_floors_at_ceiling_not_truncation(self):
        """base_cost=7, modifier=-4: (7-4)=3 vs correct floor ceil(7/2)=4."""
        nation = {
            "technology_cost_modifier": -4,
            "technologies": {"odd_cost_tech": {"cost": 3, "researched": False}},
        }
        with _patched():
            _update_tech_costs(nation)
        assert nation["technologies"]["odd_cost_tech"]["cost"] == 4

    def test_even_base_cost_unaffected_by_floor_change(self):
        """Even costs: floor and ceiling division agree, so behavior is unchanged."""
        nation = {
            "technology_cost_modifier": -4,
            "technologies": {"even_cost_tech": {"cost": 2, "researched": False}},
        }
        with _patched():
            _update_tech_costs(nation)
        assert nation["technologies"]["even_cost_tech"]["cost"] == 3  # ceil(6/2) == floor(6/2)

    def test_matches_real_lusariyya_regression_case(self):
        """Exact reproduction of the reported bug: base_cost=7, modifier=-4,
        stored cost=3 (the old, wrong floor) — must self-correct to 4."""
        nation = {
            "technology_cost_modifier": -4,
            "technology_category_cost_modifiers": {},
            "technologies": {"odd_cost_tech": {"cost": 3, "researched": False, "cost_manually_set": False}},
        }
        with _patched():
            _update_tech_costs(nation)
        assert nation["technologies"]["odd_cost_tech"]["cost"] == 4

    def test_cost_above_floor_still_computed_normally(self):
        """No modifier: cost should just be base_cost (well above floor)."""
        nation = {
            "technology_cost_modifier": 0,
            "technologies": {"odd_cost_tech": {"cost": 7, "researched": False}},
        }
        with _patched():
            _update_tech_costs(nation)
        assert nation["technologies"]["odd_cost_tech"]["cost"] == 7

    def test_category_modifier_applied_on_top_of_flat_modifier(self):
        nation = {
            "technology_cost_modifier": -1,
            "technology_category_cost_modifiers": {"culture": -1},
            "technologies": {"odd_cost_tech": {"cost": 0, "researched": False}},
        }
        with _patched():
            _update_tech_costs(nation)
        # 7 - 1 - 1 = 5, well above the floor of 4
        assert nation["technologies"]["odd_cost_tech"]["cost"] == 5

    def test_cost_manually_set_is_never_touched(self):
        nation = {
            "technology_cost_modifier": -4,
            "technologies": {"odd_cost_tech": {"cost": 1, "researched": False, "cost_manually_set": True}},
        }
        with _patched():
            _update_tech_costs(nation)
        assert nation["technologies"]["odd_cost_tech"]["cost"] == 1

    def test_one_cost_tech_floors_correctly(self):
        """base_cost=3: old wrong floor = 1, correct ceiling floor = 2."""
        nation = {
            "technology_cost_modifier": -10,
            "technologies": {"one_cost_tech": {"cost": 1, "researched": False}},
        }
        with _patched():
            _update_tech_costs(nation)
        assert nation["technologies"]["one_cost_tech"]["cost"] == 2

    def test_non_dict_technologies_is_a_noop(self):
        nation = {"technologies": "not_a_dict"}
        with _patched():
            _update_tech_costs(nation)  # must not raise
        assert nation["technologies"] == "not_a_dict"
