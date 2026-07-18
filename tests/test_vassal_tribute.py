"""
Tests for vassal tribute calculation, including the resource-scoped
vassal_tribute_flat_{resource} / vassal_tribute_multiplier_{resource} modifiers
that layer on top of the existing all-resource vassal_tribute_flat/multiplier.
"""
from calculations.field_calculations import _calc_tribute, _TRIBUTE_RESOURCES


class TestBaseTribute:
    def test_base_amount_is_pops_over_ten(self):
        result = _calc_tribute(pop_count=55, vassal_type="None", overall_total_modifiers={})
        assert result == {"food": 5, "wood": 5, "stone": 5}

    def test_minimum_base_is_one(self):
        result = _calc_tribute(pop_count=3, vassal_type="None", overall_total_modifiers={})
        assert result == {"food": 1, "wood": 1, "stone": 1}

    def test_tributary_doubles_base_and_minimum(self):
        result = _calc_tribute(pop_count=55, vassal_type="Tributary", overall_total_modifiers={})
        assert result == {"food": 10, "wood": 10, "stone": 10}

        result_low_pop = _calc_tribute(pop_count=3, vassal_type="Tributary", overall_total_modifiers={})
        assert result_low_pop == {"food": 2, "wood": 2, "stone": 2}


class TestGlobalModifiers:
    def test_global_flat_applies_to_all_resources(self):
        result = _calc_tribute(50, "None", {"vassal_tribute_flat": 3})
        assert result == {"food": 8, "wood": 8, "stone": 8}

    def test_global_multiplier_applies_to_all_resources(self):
        result = _calc_tribute(50, "None", {"vassal_tribute_multiplier": 0.5})
        assert result == {"food": 8, "wood": 8, "stone": 8}  # 5 * 1.5 = 7.5 -> rounds to 8

    def test_flat_is_added_before_multiplier(self):
        result = _calc_tribute(50, "None", {"vassal_tribute_flat": 5, "vassal_tribute_multiplier": 1.0})
        assert result == {"food": 20, "wood": 20, "stone": 20}  # (5 + 5) * 2 = 20


class TestResourceScopedModifiers:
    def test_flat_resource_only_affects_that_resource(self):
        result = _calc_tribute(50, "None", {"vassal_tribute_flat_wood": 10})
        assert result == {"food": 5, "wood": 15, "stone": 5}

    def test_multiplier_resource_only_affects_that_resource(self):
        result = _calc_tribute(50, "None", {"vassal_tribute_multiplier_stone": 1.0})
        assert result == {"food": 5, "wood": 5, "stone": 10}

    def test_resource_scoped_stacks_additively_with_global(self):
        result = _calc_tribute(50, "None", {
            "vassal_tribute_flat": 2,
            "vassal_tribute_flat_wood": 3,
        })
        # food/stone: (5+2)=7 ; wood: (5+2+3)=10
        assert result == {"food": 7, "wood": 10, "stone": 7}

    def test_resource_scoped_multiplier_stacks_additively_with_global(self):
        result = _calc_tribute(50, "None", {
            "vassal_tribute_multiplier": 0.2,
            "vassal_tribute_multiplier_food": 0.3,
        })
        # food: 5 * (1 + 0.2 + 0.3) = 7.5 -> 8 ; wood/stone: 5 * 1.2 = 6
        assert result == {"food": 8, "wood": 6, "stone": 6}

    def test_unaffected_resources_ignore_other_resources_modifiers(self):
        result = _calc_tribute(50, "None", {
            "vassal_tribute_flat_food": 100,
            "vassal_tribute_multiplier_wood": 5.0,
        })
        assert result["stone"] == 5

    def test_never_negative(self):
        result = _calc_tribute(50, "None", {"vassal_tribute_flat_wood": -100})
        assert result["wood"] == 0
        assert result["food"] == 5

    def test_all_tribute_resources_present(self):
        result = _calc_tribute(50, "None", {})
        assert set(result.keys()) == set(_TRIBUTE_RESOURCES)
