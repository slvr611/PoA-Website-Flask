"""
Regression test: the nation view page's Military tab (nation_owner.html's
read-only unit_table, used whenever editable=False — i.e. anyone who isn't
actively editing their own nation) used to list every unit the nation had
ever unlocked, even ones with 0 currently recruited — a long, mostly-empty
table for nations with many unlocked unit types.

Fixed by hiding (display:none) any unit row whose recruited count is 0 by
default, tagging it with class="unit-row-zero-count", and adding a
"Show All Units" toggle button (id="toggle-zero-units-btn") that reveals them
via a small inline script scoped to #war-section.
"""
import importlib

from bson import ObjectId
from flask import g

nr = importlib.import_module("routes.nation_routes")


def _rendered_nation_page(test_db, monkeypatch, flask_app, land_unit_details, land_units):
    from app_core import category_data
    from calculations.field_calculations import calculate_all_fields

    monkeypatch.setitem(category_data["nations"], "database", test_db["nations"])
    monkeypatch.setattr(nr, "mongo", type("M", (), {"db": test_db})())
    schema = category_data["nations"]["schema"]

    nation_id = ObjectId()
    base = {"_id": nation_id, "name": "Testland", "modifiers": []}
    calculated, breakdowns = calculate_all_fields(dict(base), schema, "nation", return_breakdowns=True)
    base.update(calculated)
    base["breakdowns"] = breakdowns
    # Override with a small, controlled unit set so the test doesn't depend
    # on which units are actually unlocked/recruitable for a bare-bones nation.
    base["land_unit_details"] = land_unit_details
    base["land_units"] = land_units
    test_db["nations"].insert_one(base)

    with flask_app.test_request_context("/nations/item/Testland"):
        g.user = None
        # Bypasses the visibility-tier gate on the Military tab (an anonymous
        # viewer otherwise sees visibility_level=0 and the whole tab is
        # locked) — matches nation_item's own getattr(g, 'is_non_player_admin',
        # False) check for admin/RP-mod full-visibility access.
        g.is_non_player_admin = True
        return nr.nation_item("Testland")


_UNIT_DETAILS = {
    "test_unit_zero": {"display_name": "Test Unit Zero", "base_stats": {}, "effective_stats": {}, "stat_breakdown": {}, "upkeep": {}},
    "test_unit_recruited": {"display_name": "Test Unit Recruited", "base_stats": {}, "effective_stats": {}, "stat_breakdown": {}, "upkeep": {}},
}


class TestZeroCountUnitsHiddenByDefaultOnNationViewPage:
    def test_zero_count_unit_row_is_tagged_hidden(self, test_db, monkeypatch, flask_app):
        html = _rendered_nation_page(
            test_db, monkeypatch, flask_app,
            _UNIT_DETAILS, {"test_unit_zero": 0, "test_unit_recruited": 5},
        )
        assert 'class="unit-row-zero-count" style="display:none;"' in html
        zero_row_pos = html.index("Test Unit Zero")
        tag_pos = html.rindex("<tr", 0, zero_row_pos)
        assert "unit-row-zero-count" in html[tag_pos:zero_row_pos]

    def test_recruited_unit_row_is_not_hidden(self, test_db, monkeypatch, flask_app):
        html = _rendered_nation_page(
            test_db, monkeypatch, flask_app,
            _UNIT_DETAILS, {"test_unit_zero": 0, "test_unit_recruited": 5},
        )
        recruited_row_pos = html.index("Test Unit Recruited")
        tag_pos = html.rindex("<tr", 0, recruited_row_pos)
        assert "unit-row-zero-count" not in html[tag_pos:recruited_row_pos]

    def test_show_all_units_button_present(self, test_db, monkeypatch, flask_app):
        html = _rendered_nation_page(
            test_db, monkeypatch, flask_app,
            _UNIT_DETAILS, {"test_unit_zero": 0, "test_unit_recruited": 5},
        )
        assert 'id="toggle-zero-units-btn"' in html
        assert "Show All Units" in html

    def test_toggle_script_targets_war_section_zero_rows(self, test_db, monkeypatch, flask_app):
        html = _rendered_nation_page(
            test_db, monkeypatch, flask_app,
            _UNIT_DETAILS, {"test_unit_zero": 0, "test_unit_recruited": 5},
        )
        assert "#war-section tr.unit-row-zero-count" in html

    def test_missing_count_defaults_to_zero_and_is_hidden(self, test_db, monkeypatch, flask_app):
        """A unit present in land_unit_details but entirely absent from the
        land_units count dict (never recruited at all) must also be hidden."""
        html = _rendered_nation_page(
            test_db, monkeypatch, flask_app,
            _UNIT_DETAILS, {"test_unit_recruited": 5},
        )
        zero_row_pos = html.index("Test Unit Zero")
        tag_pos = html.rindex("<tr", 0, zero_row_pos)
        assert "unit-row-zero-count" in html[tag_pos:zero_row_pos]
