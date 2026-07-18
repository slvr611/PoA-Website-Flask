"""
Tests for _restore_live_district_upgrades, the fix for district upgrades
reverting whenever an unrelated nation edit was submitted from a stale-loaded
edit page.

District `upgrades` are purchased via a dedicated route that $push's directly
to Mongo, bypassing the change-request system. The edit form only knows
whatever `upgrades` looked like when the page was rendered (GET time), so
submitting it later (after an upgrade was purchased in another tab/session)
would silently overwrite the live upgrades with the stale, pre-purchase list.

Critical detail these tests exercise: raw WTForms district dicts (form.data)
use `item_id` as the identity key, NOT `_id` — DistrictDict declares
`item_id = HiddenField('_id')` because WTForms cannot register underscore-
prefixed field names. `_normalize_item_ids` (called inside request_change)
renames item_id -> _id, but only AFTER this helper runs in the route. An
earlier version of this fix matched on `_id` at this stage and silently
matched nothing, since every submitted district still had `item_id`.
"""
from routes.nation_routes import _restore_live_district_upgrades


class TestRestoreLiveDistrictUpgrades:
    def test_stale_form_upgrades_replaced_with_live_value(self):
        """The real-world shape: form_data districts use `item_id` (raw WTForms
        data, pre-normalization), nation districts use `_id` (DB shape)."""
        nation = {"districts": [{"_id": "d1", "def_key": "farm", "upgrades": ["tier1", "tier2"]}]}
        form_data = {"districts": [{"item_id": "d1", "def_key": "farm", "upgrades": []}]}

        _restore_live_district_upgrades(form_data, nation)

        assert form_data["districts"][0]["upgrades"] == ["tier1", "tier2"]

    def test_also_matches_when_form_data_already_uses_underscore_id(self):
        """Defensive fallback: if this is ever called after item_id -> _id
        normalization, matching by `_id` must still work."""
        nation = {"districts": [{"_id": "d1", "upgrades": ["tier1"]}]}
        form_data = {"districts": [{"_id": "d1", "upgrades": []}]}

        _restore_live_district_upgrades(form_data, nation)

        assert form_data["districts"][0]["upgrades"] == ["tier1"]

    def test_other_district_fields_from_form_are_untouched(self):
        """Only `upgrades` is reconciled — a legitimate field edit (e.g. renaming
        the district's node) submitted on the same form must still go through."""
        nation = {"districts": [{"_id": "d1", "def_key": "farm", "node": "old_node", "upgrades": ["tier1"]}]}
        form_data = {"districts": [{"item_id": "d1", "def_key": "farm", "node": "new_node", "upgrades": []}]}

        _restore_live_district_upgrades(form_data, nation)

        assert form_data["districts"][0]["upgrades"] == ["tier1"]
        assert form_data["districts"][0]["node"] == "new_node"

    def test_matches_by_id_not_position(self):
        nation = {"districts": [
            {"_id": "d1", "upgrades": ["A"]},
            {"_id": "d2", "upgrades": ["B"]},
        ]}
        # Form submits them in a different order.
        form_data = {"districts": [
            {"item_id": "d2", "upgrades": []},
            {"item_id": "d1", "upgrades": []},
        ]}

        _restore_live_district_upgrades(form_data, nation)

        by_id = {d["item_id"]: d["upgrades"] for d in form_data["districts"]}
        assert by_id == {"d1": ["A"], "d2": ["B"]}

    def test_multiple_districts_all_reconciled(self):
        """Matches the real Lusariyya bug report: several upgraded districts
        among several un-upgraded ones, submitting an unrelated form field."""
        nation = {"districts": [
            {"_id": "d1", "def_key": "quarry", "upgrades": []},
            {"_id": "d2", "def_key": "observatory", "upgrades": ["telescope"]},
            {"_id": "d3", "def_key": "foundry", "upgrades": ["cold_struck_coinage"]},
            {"_id": "d4", "def_key": "marketplace", "upgrades": ["professional_peddlers"]},
        ]}
        form_data = {"districts": [
            {"item_id": "d1", "def_key": "quarry", "upgrades": []},
            {"item_id": "d2", "def_key": "observatory", "upgrades": []},
            {"item_id": "d3", "def_key": "foundry", "upgrades": []},
            {"item_id": "d4", "def_key": "marketplace", "upgrades": []},
        ]}

        _restore_live_district_upgrades(form_data, nation)

        by_key = {d["def_key"]: d["upgrades"] for d in form_data["districts"]}
        assert by_key == {
            "quarry": [],
            "observatory": ["telescope"],
            "foundry": ["cold_struck_coinage"],
            "marketplace": ["professional_peddlers"],
        }

    def test_newly_added_district_not_in_live_nation_is_untouched(self):
        """A district being added for the first time via this same edit has no
        live counterpart yet — its submitted upgrades (normally empty) pass through."""
        nation = {"districts": []}
        form_data = {"districts": [{"item_id": "", "upgrades": []}]}

        _restore_live_district_upgrades(form_data, nation)

        assert form_data["districts"][0]["upgrades"] == []

    def test_no_districts_field_in_form_data_is_a_noop(self):
        nation = {"districts": [{"_id": "d1", "upgrades": ["A"]}]}
        form_data = {"name": "SomeNation"}

        _restore_live_district_upgrades(form_data, nation)  # must not raise

        assert "districts" not in form_data

    def test_nation_with_no_districts_field_is_a_noop(self):
        nation = {}
        form_data = {"districts": [{"item_id": "d1", "upgrades": []}]}

        _restore_live_district_upgrades(form_data, nation)  # must not raise

        assert form_data["districts"][0]["upgrades"] == []
