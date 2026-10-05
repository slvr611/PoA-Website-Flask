"""
Regression tests for calculations.scaling_methods.per_x_district_sessions
(and its per_x_sessions_with_library wrapper).

Bug: district_session_count's field is a template ("district_sessions_
{district_key}", see json-data/modifier_types.json) resolved only at
aggregation time by field_calculations.py's sum_modifier_totals — the
stored modifier itself always has field: '' or field: None. The old
`m.get("field") == field_key` check here could therefore never match any
real stored counter, so "Per X District Sessions" scaling always returned 0
regardless of how long a district had actually been active (see the
2026-10-04 "Dyeak" incident — paired with a matching bug in
helpers/tick_helpers.py's district_duration_tick, which never incremented
the counter either, instead piling up duplicates).
"""
from calculations.scaling_methods import per_x_district_sessions, per_x_sessions_with_library


def _session_counter(district_key, value, field=""):
    return {
        "field": field, "value": value, "duration": -1,
        "source": f"District: {district_key.title()}",
        "modifier_type": "district_session_count", "district_key": district_key,
    }


class TestPerXDistrictSessions:
    def test_reads_value_from_blank_field_counter(self):
        """The realistic production shape: field is blank, not the
        literal "district_sessions_library" string."""
        target = {"modifiers": [_session_counter("library", 8)]}
        assert per_x_district_sessions(target, scaling_x=1, scaling_extra="library") == 8

    def test_also_matches_if_field_happens_to_be_set(self):
        """Backwards compatible with any legacy entry that does carry the
        literal field string."""
        target = {"modifiers": [_session_counter("library", 8, field="district_sessions_library")]}
        assert per_x_district_sessions(target, scaling_x=1, scaling_extra="library") == 8

    def test_divides_by_scaling_x(self):
        target = {"modifiers": [_session_counter("library", 9)]}
        assert per_x_district_sessions(target, scaling_x=3, scaling_extra="library") == 3

    def test_no_matching_counter_returns_zero(self):
        target = {"modifiers": [_session_counter("workshop", 5)]}
        assert per_x_district_sessions(target, scaling_x=1, scaling_extra="library") == 0

    def test_no_scaling_extra_returns_zero(self):
        target = {"modifiers": [_session_counter("library", 8)]}
        assert per_x_district_sessions(target, scaling_x=1, scaling_extra="") == 0

    def test_legacy_district_duration_source_match_still_works(self):
        target = {"modifiers": [{
            "modifier_type": "district_duration", "value": 4,
            "source": "Legacy Library tracker",
        }]}
        assert per_x_district_sessions(target, scaling_x=1, scaling_extra="library") == 4

    def test_duplicate_counters_take_the_max_not_the_sum(self):
        """Matches the pre-existing max() behavior — relevant for nations
        still carrying leftover duplicates from the tick-side bug until
        they're cleaned up; the UI shouldn't compound the corruption further
        by summing them."""
        target = {"modifiers": [_session_counter("library", 3), _session_counter("library", 8)]}
        assert per_x_district_sessions(target, scaling_x=1, scaling_extra="library") == 8

    def test_per_x_sessions_with_library_wrapper_fixes_scaling_extra(self):
        target = {"modifiers": [_session_counter("library", 6)]}
        assert per_x_sessions_with_library(target, scaling_x=2) == 3
