"""Regression tests for a real reported bug (2026-10-25): district-granted
conditional_unit_buff modifiers only ever reach a unit via the ROLE-based
prefixes _get_unit_stat_prefixes builds (melee_unit/ranged_unit/
cavalry_unit, land_melee_unit, etc.) -- never "{category}_unit" for any of
the OTHER options the admin UI's unit_category dropdown offers
(naval/cavalry/infantry/archer/siege/support/ruler/land/all). Of those,
only naval/cavalry/support/land/all happen to coincide with real prefixes;
"infantry"/"archer"/"siege" produce a field name
(e.g. "archer_unit_range") that _apply_unit_stat_modifiers can never
match, so the modifier silently does nothing, for any unit, ever.

Found on Range (unit_category: "archer") and Barracks (unit_category:
"infantry") -- fixed in district_defs by using the matching role tag
("ranged"/"melee") instead, the same three tags ("Melee", "Ranged",
"Cavalry") the unit edit form itself offers.

Fixing this surfaced a second, previously-unreachable bug: a unit's
"range" stat can be a "{min}-{max}" string (see load_db_units' range_val)
rather than a plain number, for any unit whose minimum_range !=
maximum_range (e.g. "Light Archers", several others) -- 16 such units in
production, nearly all of them ranged=True, i.e. exactly the units this
buff is meant to reach. Before this fix, no unit_category-based modifier
had ever successfully matched "range" for ANY unit (the match always
failed first), so `effective["range"] = (effective["range"] or 0) + value`
had never actually executed against a string value -- str + float raises
TypeError. _add_to_stat now adds to both ends of a range string and keeps
its string shape.
"""
from calculations.field_calculations import _apply_unit_stat_modifiers, _get_unit_stat_prefixes


def _land_unit(ranged=False, melee=False, cavalry=False, range_stat=None, hp=8):
    base_stats = {
        "attack": 5, "defense": 5, "hp": hp, "morale": 3, "damage": None,
        "retaliation_damage": None, "range": range_stat, "speed": 2, "armor": 1,
    }
    roles = {"melee": melee, "ranged": ranged, "cavalry": cavalry}
    return base_stats, roles


class TestOnlyRoleBasedCategoriesReachAUnit:
    def test_ranged_category_matches_a_ranged_unit(self):
        base_stats, roles = _land_unit(ranged=True, range_stat=2)
        tagged_sources = [{"label": "Range", "modifiers": {"ranged_unit_range": 1.0}}]
        effective, breakdown = _apply_unit_stat_modifiers(base_stats, "Land", False, False, roles, "Archer", tagged_sources)
        assert effective["range"] == 3
        assert breakdown["range"] == [{"label": "Range", "value": 1.0}]

    def test_archer_category_never_matches_anything(self):
        """The exact pre-fix bug: "archer_unit_range" is not a prefix
        _get_unit_stat_prefixes ever produces, for any unit shape."""
        base_stats, roles = _land_unit(ranged=True, range_stat=2)
        tagged_sources = [{"label": "Range", "modifiers": {"archer_unit_range": 1.0}}]
        effective, breakdown = _apply_unit_stat_modifiers(base_stats, "Land", False, False, roles, "Archer", tagged_sources)
        assert effective["range"] == 2  # unchanged
        assert breakdown == {}

    def test_melee_category_matches_a_melee_unit(self):
        base_stats, roles = _land_unit(melee=True)
        tagged_sources = [{"label": "Barracks", "modifiers": {"melee_unit_armor": 1.0}}]
        effective, breakdown = _apply_unit_stat_modifiers(base_stats, "Land", False, False, roles, "Swordsman", tagged_sources)
        assert effective["armor"] == 2

    def test_infantry_category_never_matches_anything(self):
        base_stats, roles = _land_unit(melee=True)
        tagged_sources = [{"label": "Barracks", "modifiers": {"infantry_unit_armor": 1.0}}]
        effective, breakdown = _apply_unit_stat_modifiers(base_stats, "Land", False, False, roles, "Swordsman", tagged_sources)
        assert effective["armor"] == 1  # unchanged

    def test_cavalry_category_already_worked_before_this_fix(self):
        """Stables' own buff -- cavalry happens to coincide with a real
        role flag, so it was never part of this bug."""
        base_stats, roles = _land_unit(cavalry=True)
        tagged_sources = [{"label": "Stables", "modifiers": {"cavalry_unit_speed": 1.0}}]
        effective, breakdown = _apply_unit_stat_modifiers(base_stats, "Land", False, False, roles, "Knight", tagged_sources)
        assert effective["speed"] == 3

    def test_land_ranged_unit_prefix_also_matches(self):
        """The type+role combined prefix _get_unit_stat_prefixes builds
        (land_ranged_unit) works the same as the bare role prefix."""
        base_stats, roles = _land_unit(ranged=True, range_stat=2)
        tagged_sources = [{"label": "Range", "modifiers": {"land_ranged_unit_range": 1.0}}]
        effective, _ = _apply_unit_stat_modifiers(base_stats, "Land", False, False, roles, "Archer", tagged_sources)
        assert effective["range"] == 3


class TestVariableRangeStringIsHandledSafely:
    """"Light Archers" and 15 other production units have minimum_range !=
    maximum_range, stored as a "{min}-{max}" display string rather than a
    plain number -- the exact shape that crashed before this fix."""

    def test_flat_bonus_bumps_both_ends_and_keeps_the_string_shape(self):
        base_stats, roles = _land_unit(ranged=True, range_stat="1-2")
        tagged_sources = [{"label": "Range", "modifiers": {"ranged_unit_range": 1.0}}]
        effective, breakdown = _apply_unit_stat_modifiers(base_stats, "Land", False, False, roles, "Light Archers", tagged_sources)
        assert effective["range"] == "2-3"
        assert breakdown["range"] == [{"label": "Range", "value": 1.0}]

    def test_never_raises_for_a_non_integer_delta(self):
        base_stats, roles = _land_unit(ranged=True, range_stat="1-2")
        tagged_sources = [{"label": "Range", "modifiers": {"ranged_unit_range": 0.5}}]
        effective, _ = _apply_unit_stat_modifiers(base_stats, "Land", False, False, roles, "Light Archers", tagged_sources)
        assert effective["range"] == "1.5-2.5"

    def test_plain_numeric_range_is_unaffected(self):
        """A unit whose minimum_range == maximum_range stores range as a
        plain number (see load_db_units) -- must keep working exactly as
        before this fix."""
        base_stats, roles = _land_unit(ranged=True, range_stat=3)
        tagged_sources = [{"label": "Range", "modifiers": {"ranged_unit_range": 1.0}}]
        effective, _ = _apply_unit_stat_modifiers(base_stats, "Land", False, False, roles, "Archer", tagged_sources)
        assert effective["range"] == 4
