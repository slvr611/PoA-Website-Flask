"""
Tests for the "per_x%_stability_gain_chance" scaling method (calculations/
scaling_methods.py's per_x_pct_stability_gain_chance), added for the
"United Peoples" government law (json-data/schemas/nations.json), which
scales a nation's effective_territory bonus by its own stability_gain_chance.

stability_gain_chance is stored as a 0-1 fraction (e.g. 0.5 = 50%), so a
plain per_x_-style division of the raw fraction by an integer scaling_x
would floor to 0 for almost any realistic value — the "%" in the scaling
key signals that the fraction is converted to a whole percentage (0.5 -> 50)
before dividing, which is what makes the scaling actually produce a
meaningful nonzero multiplier.
"""
from calculations.scaling_methods import (
    per_x_pct_stability_gain_chance,
    SCALING_METHODS,
    get_scaling_multiplier,
)


class TestPerXPctStabilityGainChance:
    def test_registered_under_the_percent_key(self):
        assert SCALING_METHODS.get("per_x%_stability_gain_chance") is per_x_pct_stability_gain_chance

    def test_converts_fraction_to_percentage_before_dividing(self):
        target = {"stability_gain_chance": 0.5}
        assert per_x_pct_stability_gain_chance(target, scaling_x=5) == 10  # 50% / 5

    def test_zero_chance_returns_zero(self):
        target = {"stability_gain_chance": 0}
        assert per_x_pct_stability_gain_chance(target, scaling_x=5) == 0

    def test_missing_field_defaults_to_zero(self):
        assert per_x_pct_stability_gain_chance({}, scaling_x=5) == 0

    def test_floors_to_an_integer(self):
        target = {"stability_gain_chance": 0.07}  # 7%
        assert per_x_pct_stability_gain_chance(target, scaling_x=5) == 1  # floor(7/5)

    def test_a_plain_non_percent_scaling_would_have_floored_to_zero(self):
        """Sanity check documenting exactly why the % conversion matters:
        without it, 0.5 / 5 = 0.1 -> int() = 0, useless for any nation."""
        target = {"stability_gain_chance": 0.5}
        divisor = 5
        raw_fraction_result = int(target["stability_gain_chance"] / divisor)
        assert raw_fraction_result == 0
        assert per_x_pct_stability_gain_chance(target, scaling_x=divisor) == 10

    def test_chance_above_one_is_not_clamped(self):
        """The scaling method reads whatever stability_gain_chance currently
        is — clamping/looping for actual level-gain purposes happens
        elsewhere (nation_stability_tick), not here."""
        target = {"stability_gain_chance": 1.5}
        assert per_x_pct_stability_gain_chance(target, scaling_x=5) == 30

    def test_via_get_scaling_multiplier_registry_lookup(self):
        target = {"stability_gain_chance": 0.25}
        assert get_scaling_multiplier("per_x%_stability_gain_chance", target, scaling_x=5) == 5

    def test_wired_into_united_peoples_law(self):
        from app_core import category_data
        laws = category_data["nations"]["schema"]["properties"]["government_type"]["laws"]
        united_peoples_mods = laws["United Peoples"]["_modifiers"]
        scalings_used = {m.get("scaling") for m in united_peoples_mods}
        assert "per_x%_stability_gain_chance" in scalings_used
