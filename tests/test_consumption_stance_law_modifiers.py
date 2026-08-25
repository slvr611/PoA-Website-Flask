"""
Regression tests for a real bug: the consumption_stance law's _modifiers
entries (Indulgence/Comfort/Rationing) and the slavery_stance "Labor" law's
food/wood/stone production modifiers named their target resource with
"scaling_extra" instead of "resource".

sum_modifier_totals builds a modifier's target field from the extra_fields
key that modifier_types.json declares for that modifier_type —
resource_consumption/resource_production both declare that key as literally
"resource" (see json-data/modifier_types.json), not "scaling_extra". With
the wrong key, m.get("resource") returned None, so the "{resource}" in the
"{resource}_consumption"/"{resource}_production" field template resolved to
"", producing a bogus "_consumption"/"_production" key that never touched
any nation's real food_consumption/food_production/wood_production/
stone_production. In practice, Indulgence/Comfort/Rationing's food
consumption swing and slavery Labor's resource production bonus silently
did nothing — confirmed live: a nation on Indulgence with 10 pop_count had
no "Consumption Stance: Indulgence" line in its food_consumption breakdown
at all before this fix.

"scaling_extra" is a separate, real parameter — it's what get_scaling_multiplier
passes to scaling methods that need to know *which* thing to count (e.g.
per_x_terrain_tiles' terrain type, per_x_vassals' vassal type). Renaming
these modifiers to use "resource" instead is safe specifically because
per_x_pops and per_x_slaves — the two scaling methods these laws actually
use — never read scaling_extra at all (see calculations/scaling_methods.py).
"""
from app_core import category_data
from calculations.field_calculations import sum_modifier_totals


def _get_law(stance_key, law_name):
    return category_data["nations"]["schema"]["properties"][stance_key]["laws"][law_name]


class TestConsumptionStanceLawModifiersUseTheResourceKey:
    def test_indulgence_modifier_uses_resource_not_scaling_extra(self):
        mod = _get_law("consumption_stance", "Indulgence")["_modifiers"][0]
        assert mod["modifier_type"] == "resource_consumption"
        assert mod.get("resource") == "food"
        assert "scaling_extra" not in mod

    def test_comfort_modifier_uses_resource_not_scaling_extra(self):
        mod = _get_law("consumption_stance", "Comfort")["_modifiers"][0]
        assert mod.get("resource") == "food"
        assert "scaling_extra" not in mod

    def test_rationing_modifier_uses_resource_not_scaling_extra(self):
        mod = _get_law("consumption_stance", "Rationing")["_modifiers"][0]
        assert mod.get("resource") == "food"
        assert "scaling_extra" not in mod

    def test_slavery_labor_production_modifiers_use_resource_not_scaling_extra(self):
        mods = _get_law("slavery_stance", "Labor")["_modifiers"]
        production_mods = [m for m in mods if m["modifier_type"] == "resource_production"]
        assert {m.get("resource") for m in production_mods} == {"food", "wood", "stone"}
        assert all("scaling_extra" not in m for m in production_mods)


class TestSumModifierTotalsResolvesLawStyleResourceModifiers:
    """Pins the underlying mechanism directly, independent of the schema
    content above."""

    def test_resource_key_produces_the_correctly_named_field(self):
        target = {"pop_count": 10}
        modifiers = [{
            "modifier_type": "resource_consumption", "resource": "food",
            "value": 1, "scope": "nation_self", "scaling": "per_x_pops", "scaling_x": 2,
        }]
        assert sum_modifier_totals(modifiers, target) == {"food_consumption": 5}

    def test_scaling_extra_alone_reproduces_the_historical_bug(self):
        """The old, wrong shape: using "scaling_extra" instead of "resource"
        produces a useless "_consumption" key instead of "food_consumption",
        so the value never reaches any nation's real resource consumption."""
        target = {"pop_count": 10}
        modifiers = [{
            "modifier_type": "resource_consumption", "scaling_extra": "food",
            "value": 1, "scope": "nation_self", "scaling": "per_x_pops", "scaling_x": 2,
        }]
        totals = sum_modifier_totals(modifiers, target)
        assert "food_consumption" not in totals
        assert totals.get("_consumption") == 5

    def test_per_x_pops_scaling_ignores_scaling_extra_so_the_rename_is_safe(self):
        """Confirms renaming scaling_extra -> resource on these laws could
        not have changed the scaling math itself: per_x_pops (what
        Indulgence/Comfort/Rationing use) never reads scaling_extra."""
        target = {"pop_count": 10}
        with_unrelated_scaling_extra = sum_modifier_totals([{
            "modifier_type": "resource_consumption", "resource": "food",
            "scaling_extra": "something_unrelated", "value": 1,
            "scope": "nation_self", "scaling": "per_x_pops", "scaling_x": 2,
        }], target)
        without_scaling_extra = sum_modifier_totals([{
            "modifier_type": "resource_consumption", "resource": "food",
            "value": 1, "scope": "nation_self", "scaling": "per_x_pops", "scaling_x": 2,
        }], target)
        assert with_unrelated_scaling_extra == without_scaling_extra == {"food_consumption": 5}
