"""
Tests for the "luxury_resource_production" / "luxury_resource_consumption"
modifier types (json-data/modifier_types.json) and their calculation in
calculations/compute_functions.py.

Context: general/unique resources already had a category-wide escape hatch
— a literal "resource_production"/"resource_consumption" modifier key (no
resource prefix) boosts every one of them at once (see
test_merchant_resources.py's TestComputeResourceProductionWithFoodOnlyModifier).
Luxury resources are computed in a separate code path that never checked
for an equivalent, and consumption was hard-coded to 0 with no way to
override it at all. These two new modifier types give luxury resources the
same category-wide capability.
"""
from app_core import json_data
from calculations.compute_functions import compute_resource_production, compute_resource_consumption

_LUXURY_KEYS = [r["key"] for r in json_data.get("luxury_resources", [])]
_SOME_LUXURY_KEY = _LUXURY_KEYS[0]
_ANOTHER_LUXURY_KEY = _LUXURY_KEYS[1]


class TestLuxuryResourceProduction:
    def test_no_modifiers_means_zero_luxury_production(self):
        """Baseline/backward-compat: with no node count and no production
        modifiers, luxury resources still produce nothing — matching
        behavior before this modifier type existed."""
        target = {}
        production = compute_resource_production("resource_production", target, 0, {}, {})
        assert production[_SOME_LUXURY_KEY] == 0

    def test_luxury_resource_production_boosts_every_luxury_resource(self):
        target = {}
        overall_total_modifiers = {"luxury_resource_production": 3}
        production = compute_resource_production("resource_production", target, 0, {}, overall_total_modifiers)
        for key in _LUXURY_KEYS:
            assert production[key] == 3, f"{key} did not receive the category-wide bonus"

    def test_luxury_resource_production_does_not_affect_general_resources(self):
        target = {}
        overall_total_modifiers = {"luxury_resource_production": 5}
        production = compute_resource_production("resource_production", target, 0, {}, overall_total_modifiers)
        assert production["food"] == 0
        assert production["wood"] == 0

    def test_specific_luxury_resource_modifier_still_works_alongside_category_one(self):
        target = {}
        overall_total_modifiers = {
            "luxury_resource_production": 2,
            f"{_SOME_LUXURY_KEY}_production": 10,
        }
        production = compute_resource_production("resource_production", target, 0, {}, overall_total_modifiers)
        assert production[_SOME_LUXURY_KEY] == 12
        assert production[_ANOTHER_LUXURY_KEY] == 2

    def test_node_production_still_stacks_with_the_category_modifier(self):
        target = {}
        overall_total_modifiers = {
            "luxury_resource_production": 1,
            f"{_SOME_LUXURY_KEY}_nodes": 4,
        }
        production = compute_resource_production("resource_production", target, 0, {}, overall_total_modifiers)
        assert production[_SOME_LUXURY_KEY] == 5

    def test_negative_category_modifier_clamps_at_zero(self):
        target = {}
        overall_total_modifiers = {"luxury_resource_production": -10}
        production = compute_resource_production("resource_production", target, 0, {}, overall_total_modifiers)
        assert production[_SOME_LUXURY_KEY] == 0


class TestLuxuryResourceConsumption:
    def test_no_modifiers_means_zero_luxury_consumption(self):
        """Baseline/backward-compat: luxury resources previously had
        consumption hard-coded to 0 with no override mechanism — this must
        still be true by default now that it's computed instead."""
        target = {}
        consumption = compute_resource_consumption("resource_consumption", target, 0, {}, {})
        assert consumption[_SOME_LUXURY_KEY] == 0

    def test_luxury_resource_consumption_applies_to_every_luxury_resource(self):
        target = {}
        overall_total_modifiers = {"luxury_resource_consumption": 4}
        consumption = compute_resource_consumption("resource_consumption", target, 0, {}, overall_total_modifiers)
        for key in _LUXURY_KEYS:
            assert consumption[key] == 4, f"{key} did not receive the category-wide consumption"

    def test_luxury_resource_consumption_does_not_affect_general_resources(self):
        target = {}
        overall_total_modifiers = {"luxury_resource_consumption": 7}
        consumption = compute_resource_consumption("resource_consumption", target, 0, {}, overall_total_modifiers)
        assert consumption["food"] == 0
        assert consumption["wood"] == 0

    def test_specific_luxury_resource_consumption_still_works_alongside_category_one(self):
        target = {}
        overall_total_modifiers = {
            "luxury_resource_consumption": 2,
            f"{_SOME_LUXURY_KEY}_consumption": 5,
        }
        consumption = compute_resource_consumption("resource_consumption", target, 0, {}, overall_total_modifiers)
        assert consumption[_SOME_LUXURY_KEY] == 7
        assert consumption[_ANOTHER_LUXURY_KEY] == 2

    def test_negative_category_modifier_clamps_at_zero(self):
        target = {}
        overall_total_modifiers = {"luxury_resource_consumption": -10}
        consumption = compute_resource_consumption("resource_consumption", target, 0, {}, overall_total_modifiers)
        assert consumption[_SOME_LUXURY_KEY] == 0


class TestLuxuryModifierTypesRegistered:
    def test_both_modifier_types_exist_and_apply_to_nations(self):
        modifier_types = json_data.get("modifier_types", {})
        assert "luxury_resource_production" in modifier_types
        assert "luxury_resource_consumption" in modifier_types
        assert "nation" in modifier_types["luxury_resource_production"]["applicable_to"]
        assert "nation" in modifier_types["luxury_resource_consumption"]["applicable_to"]
