"""
Tests for _restore_live_district_upgrades.

District `upgrades` are purchased via a dedicated route that $push's directly
to Mongo, bypassing the change-request system. The edit form only knows
whatever `upgrades` looked like when the page was rendered (GET time), so
submitting an UNRELATED edit later (after an upgrade was purchased in another
tab/session) would silently overwrite the live upgrades with the stale,
pre-purchase list — that's the original bug this helper guards against.

But an earlier version of the fix did this unconditionally: it overwrote the
submitted `upgrades` with the live DB value for ANY matched district, with no
way to tell "stale/unrelated submission" apart from "user just deliberately
toggled this checkbox in this exact submission" — so a deliberate toggle was
silently discarded 100% of the time. `upgrades_snapshot` (see forms.py's
DistrictDict) mirrors `upgrades` exactly as rendered at GET time and is never
touched by anything except a user's checkbox toggle (via syncDistrictUpgrades
in nation_owner_edit.html). Comparing submitted `upgrades` against this
snapshot is what lets the helper tell the two cases apart:
  - submitted == snapshot  -> nothing changed here this submission -> may be
    stale -> overwrite with the live DB value.
  - submitted != snapshot  -> the user changed something in this submission
    -> trust it, even though it also differs from the live DB value.

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
        form_data = {"districts": [{"item_id": "d1", "def_key": "farm", "upgrades": [], "upgrades_snapshot": "[]"}]}

        _restore_live_district_upgrades(form_data, nation)

        assert form_data["districts"][0]["upgrades"] == ["tier1", "tier2"]

    def test_also_matches_when_form_data_already_uses_underscore_id(self):
        """Defensive fallback: if this is ever called after item_id -> _id
        normalization, matching by `_id` must still work."""
        nation = {"districts": [{"_id": "d1", "upgrades": ["tier1"]}]}
        form_data = {"districts": [{"_id": "d1", "upgrades": [], "upgrades_snapshot": "[]"}]}

        _restore_live_district_upgrades(form_data, nation)

        assert form_data["districts"][0]["upgrades"] == ["tier1"]

    def test_other_district_fields_from_form_are_untouched(self):
        """Only `upgrades` is reconciled — a legitimate field edit (e.g. renaming
        the district's node) submitted on the same form must still go through."""
        nation = {"districts": [{"_id": "d1", "def_key": "farm", "node": "old_node", "upgrades": ["tier1"]}]}
        form_data = {"districts": [{"item_id": "d1", "def_key": "farm", "node": "new_node", "upgrades": [], "upgrades_snapshot": "[]"}]}

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
            {"item_id": "d2", "upgrades": [], "upgrades_snapshot": "[]"},
            {"item_id": "d1", "upgrades": [], "upgrades_snapshot": "[]"},
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
            {"item_id": "d1", "def_key": "quarry", "upgrades": [], "upgrades_snapshot": "[]"},
            {"item_id": "d2", "def_key": "observatory", "upgrades": [], "upgrades_snapshot": "[]"},
            {"item_id": "d3", "def_key": "foundry", "upgrades": [], "upgrades_snapshot": "[]"},
            {"item_id": "d4", "def_key": "marketplace", "upgrades": [], "upgrades_snapshot": "[]"},
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
        form_data = {"districts": [{"item_id": "", "upgrades": [], "upgrades_snapshot": "[]"}]}

        _restore_live_district_upgrades(form_data, nation)

        assert form_data["districts"][0]["upgrades"] == []

    def test_no_districts_field_in_form_data_is_a_noop(self):
        nation = {"districts": [{"_id": "d1", "upgrades": ["A"]}]}
        form_data = {"name": "SomeNation"}

        _restore_live_district_upgrades(form_data, nation)  # must not raise

        assert "districts" not in form_data

    def test_nation_with_no_districts_field_is_a_noop(self):
        nation = {}
        form_data = {"districts": [{"item_id": "d1", "upgrades": [], "upgrades_snapshot": "[]"}]}

        _restore_live_district_upgrades(form_data, nation)  # must not raise

        assert form_data["districts"][0]["upgrades"] == []


class TestDeliberateToggleIsPreserved:
    """The actual bug report: flipping an upgrade checkbox in the edit form and
    submitting never made it into the resulting change — this class locks in
    the fix."""

    def test_deliberate_toggle_on_survives_even_though_it_differs_from_live(self):
        """User checks a box to ADD an upgrade. Live DB has no upgrades yet
        (nothing purchased elsewhere). The rendered snapshot was [] (matching
        live at GET time), but the user's submission now has ["tier1"] —
        that must be kept, not reverted back to the live []."""
        nation = {"districts": [{"_id": "d1", "def_key": "farm", "upgrades": []}]}
        form_data = {"districts": [{
            "item_id": "d1", "def_key": "farm",
            "upgrades": ["tier1"], "upgrades_snapshot": "[]",
        }]}

        _restore_live_district_upgrades(form_data, nation)

        assert form_data["districts"][0]["upgrades"] == ["tier1"]

    def test_deliberate_toggle_off_survives(self):
        """User unchecks a box to REMOVE an upgrade the live DB still has."""
        nation = {"districts": [{"_id": "d1", "upgrades": ["tier1", "tier2"]}]}
        form_data = {"districts": [{
            "item_id": "d1",
            "upgrades": ["tier1"], "upgrades_snapshot": '["tier1", "tier2"]',
        }]}

        _restore_live_district_upgrades(form_data, nation)

        assert form_data["districts"][0]["upgrades"] == ["tier1"]

    def test_stale_page_without_deliberate_change_still_gets_overwritten(self):
        """Contrast case: rendered snapshot matches what was live at GET time,
        submission is unchanged from that snapshot, but the live DB has since
        moved on (a purchase happened elsewhere) — this must still protect
        the purchase by using the live value, not the stale submission."""
        nation = {"districts": [{"_id": "d1", "upgrades": ["tier1", "tier2"]}]}
        form_data = {"districts": [{
            "item_id": "d1",
            "upgrades": ["tier1"], "upgrades_snapshot": '["tier1"]',
        }]}

        _restore_live_district_upgrades(form_data, nation)

        assert form_data["districts"][0]["upgrades"] == ["tier1", "tier2"]

    def test_upgrades_snapshot_key_is_always_stripped(self):
        """upgrades_snapshot is a UI-only field and must never leak into the
        persisted change/document."""
        nation = {"districts": [{"_id": "d1", "upgrades": []}]}
        form_data = {"districts": [{
            "item_id": "d1", "upgrades": ["tier1"], "upgrades_snapshot": "[]",
        }]}

        _restore_live_district_upgrades(form_data, nation)

        assert "upgrades_snapshot" not in form_data["districts"][0]

    def test_missing_snapshot_defaults_to_empty_list(self):
        """Defensive: if upgrades_snapshot is somehow absent, don't crash —
        treat it as an empty baseline."""
        nation = {"districts": [{"_id": "d1", "upgrades": ["tier1"]}]}
        form_data = {"districts": [{"item_id": "d1", "upgrades": ["tier1"]}]}

        _restore_live_district_upgrades(form_data, nation)  # must not raise

        assert form_data["districts"][0]["upgrades"] == ["tier1"]

    def test_imperial_district_snapshot_is_also_stripped_and_reconciled(self):
        """imperial_district is a single (non-list) field that reuses the same
        DistrictDict form as regular districts — it must get the same
        snapshot-stripping/reconciliation, not just the `districts` list."""
        nation = {"districts": [], "imperial_district": {"upgrades": ["a", "b"]}}
        form_data = {"districts": [], "imperial_district": {
            "upgrades": [], "upgrades_snapshot": "[]",
        }}

        _restore_live_district_upgrades(form_data, nation)

        assert "upgrades_snapshot" not in form_data["imperial_district"]
        assert form_data["imperial_district"]["upgrades"] == ["a", "b"]

    def test_imperial_district_deliberate_toggle_survives(self):
        nation = {"districts": [], "imperial_district": {"upgrades": []}}
        form_data = {"districts": [], "imperial_district": {
            "upgrades": ["new_upgrade"], "upgrades_snapshot": "[]",
        }}

        _restore_live_district_upgrades(form_data, nation)

        assert form_data["imperial_district"]["upgrades"] == ["new_upgrade"]

    def test_malformed_snapshot_json_falls_back_gracefully(self):
        nation = {"districts": [{"_id": "d1", "upgrades": ["tier1"]}]}
        form_data = {"districts": [{
            "item_id": "d1", "upgrades": [], "upgrades_snapshot": "not valid json",
        }]}

        _restore_live_district_upgrades(form_data, nation)  # must not raise

        # snapshot falls back to [] -> submitted [] matches -> treated as stale -> overwritten
        assert form_data["districts"][0]["upgrades"] == ["tier1"]
