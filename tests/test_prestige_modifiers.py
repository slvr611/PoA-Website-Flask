"""
Tests for calculate_prestige_modifiers (calculations/field_calculations.py)
and its data file, json-data/prestige_tiers.json.

Rebalance context: the tier table used to be hardcoded as a chain of
prestige > N comparisons. It's now read from prestige_tiers.json (an
ordered list of tier objects keyed by range) so it can be edited without
touching code. This also folded in a balance update to the tier boundaries
and values — two changes from the old hardcoded table are worth calling
out directly:
  - 31-40 no longer has a Strength (Attack/Defense) penalty, but gains
    effective_pop_capacity +1 (previously 0 at this tier).
  - 61-70 no longer has a Strength bonus (it now only starts at 71-80), and
    81-90's Strength bonus dropped from +2 to +1 (only 91-100 keeps +2).

Not covered here (see calculate_prestige_modifiers's docstring): each
tier's build_cost_reduction_pct is present in the data file but
deliberately not turned into a modifier yet, since nothing in the codebase
applies a cost modifier to district/city/wall/wonder build costs at all.
"""
from app_core import json_data
from calculations.field_calculations import calculate_prestige_modifiers, _select_prestige_tier

_SCHEMA_PROPS = {
    "government_type": {
        "laws": {
            "Settled": {"nomadic": 0},
            "Nomadic": {"nomadic": 1},
        }
    }
}


def _nation(prestige, nomadic=False):
    return {
        "prestige": prestige,
        "government_type": "Nomadic" if nomadic else "Settled",
    }


class TestPrestigeTiersDataFile:
    def test_nine_tiers_covering_1_through_100(self):
        tiers = json_data["prestige_tiers"]
        assert len(tiers) == 9
        assert tiers[0]["range"] == "1-10"
        assert tiers[-1]["range"] == "91-100"

    def test_tiers_are_contiguous_and_ascending(self):
        tiers = json_data["prestige_tiers"]
        for prev, nxt in zip(tiers, tiers[1:]):
            assert nxt["min_prestige"] == prev["max_prestige"] + 1


class TestSelectPrestigeTier:
    def test_values_within_a_middle_tier(self):
        tiers = json_data["prestige_tiers"]
        assert _select_prestige_tier(tiers, 45)["range"] == "41-60"

    def test_below_the_lowest_tier_still_gets_the_lowest_tier(self):
        """Imperial Collapse territory — prestige can drop below 1."""
        tiers = json_data["prestige_tiers"]
        assert _select_prestige_tier(tiers, -5)["range"] == "1-10"
        assert _select_prestige_tier(tiers, 0)["range"] == "1-10"

    def test_above_the_highest_tier_still_gets_the_highest_tier(self):
        tiers = json_data["prestige_tiers"]
        assert _select_prestige_tier(tiers, 500)["range"] == "91-100"

    def test_boundary_values_match_the_correct_side(self):
        tiers = json_data["prestige_tiers"]
        assert _select_prestige_tier(tiers, 10)["range"] == "1-10"
        assert _select_prestige_tier(tiers, 11)["range"] == "11-20"
        assert _select_prestige_tier(tiers, 90)["range"] == "81-90"
        assert _select_prestige_tier(tiers, 91)["range"] == "91-100"


class TestCalculatePrestigeModifiersAgainstTheSpec:
    def test_tier_1_10(self):
        mods = calculate_prestige_modifiers(_nation(5), _SCHEMA_PROPS)
        assert mods == {
            "karma": -8, "stability_loss_chance": 0.25,
            "strength": -3, "effective_pop_capacity": -2,
        }

    def test_tier_11_20(self):
        mods = calculate_prestige_modifiers(_nation(15), _SCHEMA_PROPS)
        assert mods == {
            "karma": -6, "stability_loss_chance": 0.20,
            "strength": -2, "effective_pop_capacity": -1,
            "effective_territory": 10,
        }

    def test_tier_21_30(self):
        mods = calculate_prestige_modifiers(_nation(25), _SCHEMA_PROPS)
        assert mods == {
            "karma": -4, "stability_loss_chance": 0.15,
            "strength": -1, "effective_territory": 15,
        }

    def test_tier_31_40_has_no_strength_penalty_and_a_pop_bonus(self):
        """The balance change: this tier used to carry strength=-1 and no
        pop bonus; the new spec drops the strength penalty entirely and
        adds +1 effective_pop_capacity instead."""
        mods = calculate_prestige_modifiers(_nation(35), _SCHEMA_PROPS)
        assert "strength" not in mods
        assert mods == {
            "karma": -2, "stability_loss_chance": 0.10,
            "effective_pop_capacity": 1, "effective_territory": 25,
        }

    def test_tier_41_60(self):
        mods = calculate_prestige_modifiers(_nation(50), _SCHEMA_PROPS)
        assert mods == {
            "stability_gain_chance": 0.05,
            "effective_pop_capacity": 2, "effective_territory": 30,
        }

    def test_tier_61_70_no_longer_has_a_strength_bonus(self):
        """The balance change: this tier used to carry strength=1; the new
        spec moves the first Strength bonus to 71-80."""
        mods = calculate_prestige_modifiers(_nation(65), _SCHEMA_PROPS)
        assert "strength" not in mods
        assert mods == {
            "stability_gain_chance": 0.10,
            "effective_pop_capacity": 3, "effective_territory": 35,
        }

    def test_tier_71_80(self):
        mods = calculate_prestige_modifiers(_nation(75), _SCHEMA_PROPS)
        assert mods == {
            "karma": 2, "stability_gain_chance": 0.15, "strength": 1,
            "effective_pop_capacity": 4, "effective_territory": 40,
        }

    def test_tier_81_90_strength_bonus_reduced_to_plus_one(self):
        """The balance change: this tier used to carry strength=2; the new
        spec caps it at +1 (only 91-100 keeps +2)."""
        mods = calculate_prestige_modifiers(_nation(85), _SCHEMA_PROPS)
        assert mods["strength"] == 1
        assert mods == {
            "karma": 4, "stability_gain_chance": 0.20, "strength": 1,
            "effective_pop_capacity": 5, "effective_territory": 45,
        }

    def test_tier_91_100(self):
        mods = calculate_prestige_modifiers(_nation(95), _SCHEMA_PROPS)
        assert mods == {
            "karma": 6, "stability_gain_chance": 0.25, "strength": 2,
            "effective_pop_capacity": 6, "effective_territory": 50,
        }


class TestNomadicTerritoryVariant:
    def test_nomadic_uses_the_nomadic_territory_value(self):
        mods = calculate_prestige_modifiers(_nation(95, nomadic=True), _SCHEMA_PROPS)
        assert mods["effective_territory"] == 25

    def test_settled_uses_the_normal_territory_value(self):
        mods = calculate_prestige_modifiers(_nation(95, nomadic=False), _SCHEMA_PROPS)
        assert mods["effective_territory"] == 50

    def test_nomadic_tier_1_10_has_no_territory_either_way(self):
        mods = calculate_prestige_modifiers(_nation(5, nomadic=True), _SCHEMA_PROPS)
        assert "effective_territory" not in mods


class TestBuildCostReductionIsIntentionallyNotWiredUpYet:
    def test_data_file_carries_the_value(self):
        tier = next(t for t in json_data["prestige_tiers"] if t["range"] == "91-100")
        assert tier["build_cost_reduction_pct"] == 25

    def test_modifier_output_does_not_include_it(self):
        """Documents the deliberate gap — see calculate_prestige_modifiers's
        docstring. If this starts failing because someone wired cost
        reduction into collected modifiers, also verify every district/
        city/wall/wonder purchase call site actually applies it before
        updating this test."""
        mods = calculate_prestige_modifiers(_nation(95), _SCHEMA_PROPS)
        assert not any("cost_reduction" in k for k in mods)
