"""Regression test for a real reported bug (2026-10-25, Dyeak's Workshop):
the "Per X District Sessions" land attack/damage buffs on warfare
districts (workshop, forge, ...) use a CONDITIONAL modifier --
conditional_unit_buff with condition_scaling: "per_x_district_sessions",
gating a flat land attack/damage bonus behind a >=5 / >=10 session
threshold -- never the modifier's own VALUE-scaling field.

DistrictAdapter.collect already auto-fills `scaling_extra` with the
district's own def_key when a modifier's `scaling` is
"per_x_district_sessions", so an admin authoring the district def doesn't
have to manually type it in -- but that auto-fill only ever checked
`scaling`/`scaling_extra`, never the separate `condition_scaling`/
`condition_scaling_extra` pair a CONDITIONAL modifier uses instead.
per_x_district_sessions (calculations/scaling_methods.py) returns 0
outright when scaling_extra is blank, so the condition
(actual_sessions >= 5) was comparing 0 >= 5 -- always False, regardless of
how many sessions had actually accumulated. Workshop's land attack bonus
could never apply, for any nation, ever.

Fixed by extending the auto-fill to also cover condition_scaling_extra,
for both a district's own modifiers and its upgrades' modifiers.
"""
from unittest.mock import patch, MagicMock

from calculations.source_adapters import DistrictAdapter


def _collect(dd_modifiers, nation_modifiers, upgrades=None, district_overrides=None):
    """dd_modifiers: the DISTRICT DEFINITION's own modifiers (e.g. the
    conditional_unit_buff entries) -- district_defs.modifiers in real data.
    nation_modifiers: the NATION's own modifiers array (e.g. the
    district_session_count tracking entries district_duration_tick
    maintains) -- per_x_district_sessions reads from target["modifiers"],
    never from the district definition."""
    fake_dd = {
        "key": "workshop", "display_name": "Workshop", "modifiers": dd_modifiers,
        "upgrades": upgrades or [],
    }
    mock_mongo = MagicMock()
    # _resolve_def resolves def_key lookups via a bulk find({}) cache
    # (see _get_cached_district_defs_by_key) rather than a per-key find_one.
    mock_mongo.db.district_defs.find.return_value = [fake_dd]
    district = {"def_key": "workshop", "_id": "d1"}
    district.update(district_overrides or {})
    target = {"name": "Test Nation", "districts": [district], "modifiers": nation_modifiers}
    with patch("calculations.field_calculations.mongo", mock_mongo):
        contributions = DistrictAdapter.collect(target, {"properties": {}}, {}, {}, {})
    merged = {}
    for c in contributions:
        for k, v in c.modifiers.items():
            merged[k] = merged.get(k, 0) + v
    return merged


def _session_count(district_key, value):
    return {"modifier_type": "district_session_count", "district_key": district_key, "value": value}


def _buff(unit_stat, threshold, extra=None):
    mod = {
        "modifier_type": "conditional_unit_buff", "value": 1.0,
        "unit_category": "land", "unit_stat": unit_stat, "scope": "nation_self",
        "condition_scaling": "per_x_district_sessions", "condition_operator": ">=",
        "condition_value": threshold, "condition_scaling_x": 1.0,
    }
    if extra is not None:
        mod["condition_scaling_extra"] = extra
    return mod


class TestConditionScalingExtraAutofill:
    def test_the_real_workshop_land_attack_buff_applies_at_5_sessions(self):
        """The exact live modifier shape from workshop's district_defs
        entry (condition_scaling_extra deliberately omitted, exactly as
        authored) -- Dyeak's workshop sat at 5.0 recorded sessions."""
        result = _collect([_buff("attack", 5.0)], [_session_count("workshop", 5.0)])
        assert result.get("land_unit_attack", 0) == 1.0

    def test_does_not_apply_below_the_threshold(self):
        result = _collect([_buff("attack", 5.0)], [_session_count("workshop", 4.0)])
        assert result.get("land_unit_attack", 0) == 0

    def test_the_damage_buff_needs_its_own_higher_threshold(self):
        """Workshop's second conditional buff (>=10 sessions) must stay
        gated independently of the first (>=5)."""
        dd_mods = [_buff("attack", 5.0), _buff("damage", 10.0)]

        result = _collect(dd_mods, [_session_count("workshop", 5.0)])
        assert result.get("land_unit_attack", 0) == 1.0
        assert result.get("land_unit_damage", 0) == 0

        result = _collect(dd_mods, [_session_count("workshop", 10.0)])
        assert result.get("land_unit_attack", 0) == 1.0
        assert result.get("land_unit_damage", 0) == 1.0

    def test_an_explicit_condition_scaling_extra_is_never_overridden(self):
        """If a modifier is deliberately authored to key off a DIFFERENT
        district (e.g. a cross-district synergy), the auto-fill must never
        clobber that explicit value with this district's own def_key."""
        result = _collect(
            [_buff("attack", 5.0, extra="forge")],
            [_session_count("forge", 5.0)],
        )
        assert result.get("land_unit_attack", 0) == 1.0

    def test_upgrade_modifiers_get_the_same_autofill(self):
        upgrades = [{
            "key": "reinforced", "display_name": "Reinforced",
            "modifiers": [_buff("attack", 5.0)],
        }]
        # The upgrade modifier's value is 1.0 from _buff's default -- bump
        # it to confirm this result actually came from the upgrade, not a
        # stray base-modifier contribution.
        upgrades[0]["modifiers"][0]["value"] = 2.0

        result = _collect(
            [], [_session_count("workshop", 5.0)],
            upgrades=upgrades, district_overrides={"upgrades": ["reinforced"]},
        )
        assert result.get("land_unit_attack", 0) == 2.0
