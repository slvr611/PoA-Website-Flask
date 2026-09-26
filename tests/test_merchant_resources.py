"""
Regression tests for the merchant resource-production/capacity bug fixes:

- The "the_countryman"/"the_rustic"/"the_green" titles (and their negative
  mirrors) used to grant a generic "merchant_resource_production" modifier,
  which strips (via calculate_title_modifiers) to the flat "resource_production"
  key and therefore boosted EVERY resource a merchant tracks (food, wood,
  research, gunpowder, ...) instead of just food. That key has been renamed
  to "merchant_food_production" to match the correctly-scoped nation-side
  "nation_food_production" sibling.
- Merchants must never be able to produce or store resources flagged
  "merchant_ineligible" (currently just Research), regardless of what a
  modifier grants.
- Merchants now have a resource_capacity field (compute_merchant_resource_storage_capacity)
  mirroring the nation/market capacity pattern.
- The "resource_storage_capacity" modifier_type (json-data/modifier_types.json)
  was applicable_to nation-only, even though compute_merchant_resource_
  storage_capacity already read the exact same {resource}_storage_capacity /
  flat resource_storage_capacity keys — the calculation engine was fully
  ready, but admins had no way to attach the modifier to a merchant's own
  modifiers array via the UI (its applicable_to filter excluded "merchant").
  Fixed by adding "merchant" to applicable_to.
"""
from app_core import app, category_data, json_data
from calculations.compute_functions import (
    compute_resource_production,
    compute_merchant_resource_storage_capacity,
)
from calculations.field_calculations import calculate_title_modifiers, calculate_all_fields


class TestTitleModifierKeyFix:
    """Expected values are read from json-data/positive_titles.json itself
    rather than hardcoded, so rebalancing "the_countryman"'s numbers doesn't
    break this test — what's actually being checked is that
    calculate_title_modifiers strips the nation_/merchant_ prefix onto the
    correctly-scoped "food_production" key (not a generic
    "resource_production" key boosting every resource), regardless of what
    the current tuned values are.
    """

    def test_merchant_title_only_boosts_food(self):
        expected = json_data["positive_titles"]["the_countryman"]["modifiers"]["merchant_food_production"]
        mods = calculate_title_modifiers(["the_countryman"], "merchant", {})
        assert mods == {"food_production": expected}
        assert "resource_production" not in mods

    def test_nation_title_only_boosts_food(self):
        expected = json_data["positive_titles"]["the_countryman"]["modifiers"]["nation_food_production"]
        mods = calculate_title_modifiers(["the_countryman"], "nation", {})
        assert mods == {"food_production": expected}


class TestComputeResourceProductionWithFoodOnlyModifier:
    def test_food_production_key_only_affects_food(self):
        target = {}  # no "name" key -> skips trade-route lookup
        overall_total_modifiers = {"food_production": 2}
        production = compute_resource_production(
            "resource_production", target, 0, {}, overall_total_modifiers
        )
        assert production["food"] == 2
        assert production["wood"] == 0
        assert production["research"] == 0
        assert production["gunpowder"] == 0

    def test_generic_resource_production_key_still_boosts_everything(self):
        # Documents the historical footgun: a literal "resource_production" key
        # (with no resource prefix) legitimately applies to all resources by
        # design in compute_resource_production. The bug was that the title's
        # modifier key stripped down to exactly this generic key for merchants;
        # the fix was renaming the title's key, not changing this function.
        target = {}
        overall_total_modifiers = {"resource_production": 2}
        production = compute_resource_production(
            "resource_production", target, 0, {}, overall_total_modifiers
        )
        assert production["food"] == 2
        assert production["gunpowder"] == 2
        assert production["research"] == 2


class TestMerchantResourceCapacity:
    def test_base_storage_used_when_no_modifiers(self):
        capacity = compute_merchant_resource_storage_capacity(
            "resource_capacity", {}, 0, {}, {}
        )
        assert capacity["food"] == 25
        assert capacity["iron"] == 10
        assert capacity["gunpowder"] == 0
        assert capacity["research"] == 0

    def test_merchant_ineligible_resource_always_zero_even_with_modifier(self):
        capacity = compute_merchant_resource_storage_capacity(
            "resource_capacity", {}, 0, {}, {"research_storage_capacity": 50}
        )
        assert capacity["research"] == 0

    def test_specific_storage_capacity_modifier_applies(self):
        capacity = compute_merchant_resource_storage_capacity(
            "resource_capacity", {}, 0, {}, {"iron_storage_capacity": 5}
        )
        assert capacity["iron"] == 15


class TestResourceStorageCapacityModifierApplicableToMerchant:
    def test_modifier_type_lists_merchant_as_applicable(self):
        assert "merchant" in json_data["modifier_types"]["resource_storage_capacity"]["applicable_to"]
        assert "nation" in json_data["modifier_types"]["resource_storage_capacity"]["applicable_to"]

    def test_merchant_self_scoped_modifier_increases_capacity_end_to_end(self):
        """The full admin-editor path: a modifier_type/scope entry on a
        merchant's own modifiers array, run through calculate_all_fields —
        not just calling compute_merchant_resource_storage_capacity
        directly with a pre-built totals dict."""
        schema = category_data["merchants"]["schema"]
        merchant = {
            "name": "Test Merchant",
            "modifiers": [
                {"modifier_type": "resource_storage_capacity", "resource": "gold",
                 "value": 25, "scope": "merchant_self", "scaling": "flat"},
            ],
        }
        with app.app_context():
            calc = calculate_all_fields(dict(merchant), schema, "merchant")

        base_gold = compute_merchant_resource_storage_capacity("resource_capacity", {}, 0, {}, {})["gold"]
        assert calc["resource_capacity"]["gold"] == base_gold + 25
        # An unrelated resource must be untouched.
        base_food = compute_merchant_resource_storage_capacity("resource_capacity", {}, 0, {}, {})["food"]
        assert calc["resource_capacity"]["food"] == base_food
