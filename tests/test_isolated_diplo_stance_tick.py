"""
Tests for helpers.tick_helpers.isolated_diplo_stance_tick and the schema
fields it depends on.

Root cause of the "stuck at 0 forever" bug: isolated_stab_gain_rate/
isolated_stab_gain_max are law-driven values (from the "Isolated"
diplomatic_stance law block in json-data/schemas/nations.json) that must be
merged into overall_total_modifiers via a "calculated" schema field to ever
reach the nation document. They were missing "calculated": true entirely, so
the tick's `old_nation.get("isolated_stab_gain_rate", 0)` always read the
default 0. Marking them calculated surfaced a second bug: compute_field_default
truncates any non-percentage-formatted field to int(), which floors 0.1/0.5
straight to 0. Both had to be fixed (calculated: true AND format: percentage).
"""
import json
from copy import deepcopy

from calculations.compute_functions import compute_field_default
from helpers.tick_helpers import isolated_diplo_stance_tick


def _nation(**overrides):
    nation = {
        "name": "Test Nation",
        "diplomatic_stance": "Isolated",
        "isolated_stab_gain_rate": 0.1,
        "isolated_stab_gain_max": 0.5,
        "modifiers": [],
    }
    nation.update(overrides)
    return nation


class TestSchemaFieldsAvoidIntTruncation:
    def test_isolated_stab_gain_fields_are_calculated_and_percentage(self):
        with open("json-data/schemas/nations.json", encoding="utf-8") as f:
            raw = json.load(f)
        props = raw["$jsonSchema"]["properties"]
        for field in ("isolated_stab_gain_rate", "isolated_stab_gain_max"):
            assert props[field].get("calculated") is True, f"{field} must be calculated to receive law modifiers"
            assert props[field].get("format") == "percentage", f"{field} must not be int-truncated by compute_field_default"

    def test_compute_field_default_does_not_truncate_percentage_fields(self):
        value = compute_field_default(
            "isolated_stab_gain_rate", {}, 0,
            {"format": "percentage"},
            {"isolated_stab_gain_rate": 0.1},
        )
        assert value == 0.1

    def test_compute_field_default_truncates_non_percentage_fields(self):
        # Documents the actual footgun: without "format": "percentage", any
        # fractional law/modifier value silently floors to an integer.
        value = compute_field_default(
            "isolated_stab_gain_rate", {}, 0,
            {},
            {"isolated_stab_gain_rate": 0.1},
        )
        assert value == 0


class TestIsolatedDiploStanceTick:
    def test_creates_modifier_on_first_isolated_tick(self):
        old_nation = _nation()
        new_nation = deepcopy(old_nation)
        result = isolated_diplo_stance_tick(old_nation, new_nation, {})
        assert "increased from 0 to 0.1" in result
        mods = [m for m in new_nation["modifiers"] if m.get("source") == "Isolated Diplomatic Stance"]
        assert len(mods) == 1
        assert mods[0]["value"] == 0.1

    def test_increments_existing_modifier_each_tick(self):
        old_nation = _nation(modifiers=[
            {"_id": "abc123", "field": "stability_gain_chance", "value": 0.2, "duration": -1, "source": "Isolated Diplomatic Stance"}
        ])
        new_nation = deepcopy(old_nation)
        result = isolated_diplo_stance_tick(old_nation, new_nation, {})
        assert "increased from 0.2 to 0.30000000000000004" not in result  # rounded, not raw float
        assert "increased from 0.2 to 0.3" in result
        mods = [m for m in new_nation["modifiers"] if m.get("source") == "Isolated Diplomatic Stance"]
        assert mods[0]["value"] == 0.3

    def test_caps_at_isolated_stab_gain_max(self):
        old_nation = _nation(modifiers=[
            {"_id": "abc123", "field": "stability_gain_chance", "value": 0.45, "duration": -1, "source": "Isolated Diplomatic Stance"}
        ])
        new_nation = deepcopy(old_nation)
        isolated_diplo_stance_tick(old_nation, new_nation, {})
        mods = [m for m in new_nation["modifiers"] if m.get("source") == "Isolated Diplomatic Stance"]
        assert mods[0]["value"] == 0.5

        # Running it again once already at the cap must not exceed it.
        old_nation_2 = _nation(modifiers=deepcopy(new_nation["modifiers"]))
        new_nation_2 = deepcopy(old_nation_2)
        isolated_diplo_stance_tick(old_nation_2, new_nation_2, {})
        mods_2 = [m for m in new_nation_2["modifiers"] if m.get("source") == "Isolated Diplomatic Stance"]
        assert mods_2[0]["value"] == 0.5

    def test_zero_gain_rate_never_grows_the_modifier(self):
        # Regression guard for the original bug: if the law data ever fails
        # to reach the nation again, the modifier should visibly stay at 0
        # rather than silently vanishing, making the bug easy to spot again.
        old_nation = _nation(isolated_stab_gain_rate=0, isolated_stab_gain_max=0, modifiers=[
            {"_id": "abc123", "field": "stability_gain_chance", "value": 0, "duration": -1, "source": "Isolated Diplomatic Stance"}
        ])
        new_nation = deepcopy(old_nation)
        isolated_diplo_stance_tick(old_nation, new_nation, {})
        mods = [m for m in new_nation["modifiers"] if m.get("source") == "Isolated Diplomatic Stance"]
        assert mods[0]["value"] == 0

    def test_removes_modifier_when_no_longer_isolated(self):
        old_nation = _nation(diplomatic_stance="Cooperative", stability_gain_chance=0.35, modifiers=[
            {"_id": "abc123", "field": "stability_gain_chance", "value": 0.3, "duration": -1, "source": "Isolated Diplomatic Stance"}
        ])
        new_nation = deepcopy(old_nation)
        result = isolated_diplo_stance_tick(old_nation, new_nation, {})
        assert "removed" in result
        remaining = [m for m in new_nation["modifiers"] if m.get("source") == "Isolated Diplomatic Stance"]
        assert remaining == []
