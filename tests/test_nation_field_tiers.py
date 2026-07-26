"""
Tests for the per-field nation visibility tier registry
(NATION_VIEW_FIELD_TIERS / nation_field_tier) used to gate individual
fields/sections on the nation view and edit pages.
"""
from helpers.visibility_helpers import NATION_VIEW_FIELD_TIERS, nation_field_tier


class TestNationFieldTier:
    def test_known_field_returns_registered_tier(self):
        assert nation_field_tier("money") == 4
        assert nation_field_tier("primary_race") == 0
        assert nation_field_tier("pops") == 1
        assert nation_field_tier("stability") == 2
        assert nation_field_tier("districts") == 3

    def test_unknown_field_returns_default(self):
        assert nation_field_tier("some_made_up_field") == 0
        assert nation_field_tier("some_made_up_field", default=2) == 2

    def test_money_is_more_restricted_than_trade_logistics(self):
        # Regression guard for the bug that prompted this registry: money
        # must require a strictly higher tier than general trade info.
        assert nation_field_tier("money") > nation_field_tier("trade_routes")
        assert nation_field_tier("money_income") > nation_field_tier("import_slots")

    def test_all_registered_tiers_are_within_valid_range(self):
        for field, tier in NATION_VIEW_FIELD_TIERS.items():
            assert isinstance(field, str)
            assert tier in (0, 1, 2, 3, 4), f"{field} has invalid tier {tier}"

    def test_military_and_detailed_economy_are_tier_four(self):
        for field in ("land_units", "naval_units", "resource_production", "money"):
            assert nation_field_tier(field) == 4
