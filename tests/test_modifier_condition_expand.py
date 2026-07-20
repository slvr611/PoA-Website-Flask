"""
Tests for the "Condition" details block's expand-by-default logic in
templates/_modifier_macros.html.

Bug: the details block opened whenever condition_scaling was non-empty, but
many existing modifiers have condition_scaling stored as "flat" (the same
sentinel used for "no scaling" on the main scaling type) with no real
condition_value ever set — a leftover/default state, not a genuine condition.
"flat" must be treated the same as "" for expand-state purposes; any other
real scaling type (e.g. "per_x_pops") should still expand the block.
"""
from app_core import app


def _render_open_state(condition_scaling):
    """Render just the boolean expression used to gate the details' `open`
    attribute, exactly as it appears in _modifier_macros.html."""
    template = app.jinja_env.from_string(
        "{% set cur_cond_sc = condition_scaling or '' %}"
        "{% if cur_cond_sc and cur_cond_sc != 'flat' %}OPEN{% else %}CLOSED{% endif %}"
    )
    return template.render(condition_scaling=condition_scaling)


class TestConditionDetailsExpandState:
    def test_empty_string_stays_closed(self):
        assert _render_open_state("") == "CLOSED"

    def test_none_stays_closed(self):
        assert _render_open_state(None) == "CLOSED"

    def test_flat_stays_closed(self):
        """The actual bug: 'flat' is a leftover default, not a real condition."""
        assert _render_open_state("flat") == "CLOSED"

    def test_real_scaling_type_opens(self):
        assert _render_open_state("per_x_pops") == "OPEN"

    def test_district_scaling_type_opens(self):
        assert _render_open_state("per_x_district_sessions") == "OPEN"


def test_macro_file_applies_flat_exclusion_in_both_render_paths():
    """Guard against regressing to a version that only fixes one of the two
    "Condition" details occurrences (dict-mode vs WTForms FieldList-mode)."""
    with open("templates/_modifier_macros.html", encoding="utf-8") as f:
        content = f.read()
    occurrences = content.count(
        'if cur_cond_sc and cur_cond_sc != \'flat\''
    )
    assert occurrences == 2, (
        "Expected both the dict-mode and FieldList-mode Condition details "
        "blocks to exclude 'flat' from the expand check"
    )
