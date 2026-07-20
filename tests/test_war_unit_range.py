"""
Tests for routes.war_routes._coerce_unit_range.

Bug report: the war item page for "Blood on the Green Plains" 500'd with
    ValueError: invalid literal for int() with base 10: '1-2'
because calculations/field_calculations.py computes a unit's base_stats
"range" as a "min-max" display string (e.g. "1-2") when the unit has
distinct minimum_range/maximum_range values, but _unit_types_for_nation
unconditionally called int() on it, assuming range is always a plain number.
"""
from routes.war_routes import _coerce_unit_range


class TestCoerceUnitRange:
    def test_none_defaults_to_one(self):
        assert _coerce_unit_range(None) == 1

    def test_plain_int_passes_through(self):
        assert _coerce_unit_range(3) == 3

    def test_min_max_range_string_passes_through_unchanged(self):
        """The actual bug: a variable-range unit's "1-2" string must not
        raise, and should still display the full range, not just one end."""
        assert _coerce_unit_range("1-2") == "1-2"

    def test_float_is_coerced_to_int(self):
        assert _coerce_unit_range(2.0) == 2
