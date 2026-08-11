"""
Regression tests for helpers.tick_helpers.district_duration_tick.

Production crash: AttributeError: 'NoneType' object has no attribute
'startswith', from `m.get("field", "")` — that default only applies when the
key is *missing*, not when it's present with value None. 28 real nations
carry structured (modifier_type-based) modifiers that legitimately have
field: None, which crashed the tick every time it ran for one of them. The
tick's new atomic-commit machinery caught this cleanly (rolled everything
back instead of partially applying), but the underlying bug still needed
fixing.
"""
from unittest.mock import patch

import helpers.tick_helpers as th


def _nation(modifiers, districts=None):
    return {"_id": "n1", "name": "Test Nation", "modifiers": modifiers, "districts": districts or []}


class TestFieldNoneDoesNotCrash:
    def test_structured_modifier_with_field_none_is_left_alone(self):
        """A modifier targeting via modifier_type (field: None) must not
        crash district_duration_tick, and must not be mistaken for a stale
        district_sessions_ counter."""
        modifiers = [{
            "field": None, "modifier_type": "stability_gain_chance",
            "value": 0.35, "duration": 1, "source": "https://discord.com/...",
        }]
        old_nation = _nation(modifiers)
        new_nation = _nation(list(modifiers))

        result = th.district_duration_tick(old_nation, new_nation, {})

        assert isinstance(result, str)
        assert new_nation["modifiers"] == modifiers  # untouched

    def test_modifier_missing_field_key_entirely_still_works(self):
        """The pre-existing (already-safe) case: no "field" key at all."""
        modifiers = [{"modifier_type": "import_slots", "value": -2.0, "duration": -1, "source": "s"}]
        old_nation = _nation(modifiers)
        new_nation = _nation(list(modifiers))

        result = th.district_duration_tick(old_nation, new_nation, {})

        assert isinstance(result, str)
        assert new_nation["modifiers"] == modifiers


class TestStaleCounterCleanup:
    def test_stale_district_sessions_counter_is_removed(self):
        """A district_sessions_ counter for a district the nation no longer
        has (no matching active_def_keys entry) must be removed, alongside
        an unrelated field:None modifier that must survive untouched."""
        modifiers = [
            {"field": "district_sessions_old_mine", "value": 5, "duration": -1,
             "source": "District: Old Mine", "modifier_type": "district_session_count", "district_key": "old_mine"},
            {"field": None, "modifier_type": "stability_gain_chance", "value": 0.1, "duration": -1, "source": "x"},
        ]
        old_nation = _nation(modifiers, districts=[])  # no districts -> nothing active
        new_nation = _nation([dict(m) for m in modifiers], districts=[])

        result = th.district_duration_tick(old_nation, new_nation, {})

        remaining_fields = [m.get("field") for m in new_nation["modifiers"]]
        assert "district_sessions_old_mine" not in remaining_fields
        assert None in remaining_fields  # the unrelated modifier survives
        assert "removed stale counter" in result


class TestCounterIncrement:
    def test_existing_counter_increments_for_active_district_duration_district(self):
        district = {"def_key": "sawmill"}
        modifiers = [{
            "field": "district_sessions_sawmill", "value": 3, "duration": -1,
            "source": "District: Sawmill", "modifier_type": "district_session_count", "district_key": "sawmill",
        }]
        old_nation = _nation(modifiers, districts=[district])
        new_nation = _nation([dict(m) for m in modifiers], districts=[district])

        fake_def = {
            "display_name": "Sawmill",
            "modifiers": [{"modifier_type": "duration_scaling"}],
        }
        fake_modifier_types = {"duration_scaling": {"is_district_duration": True}}

        with patch("calculations.field_calculations._resolve_def", return_value=fake_def), \
             patch.object(th, "json_data", {"modifier_types": fake_modifier_types}):
            result = th.district_duration_tick(old_nation, new_nation, {})

        counter = next(m for m in new_nation["modifiers"] if m["field"] == "district_sessions_sawmill")
        assert counter["value"] == 4
        assert "session count -> 4" in result

    def test_new_counter_created_for_active_district_with_no_prior_modifier(self):
        district = {"def_key": "sawmill"}
        old_nation = _nation([], districts=[district])
        new_nation = _nation([], districts=[district])

        fake_def = {
            "display_name": "Sawmill",
            "modifiers": [{"modifier_type": "duration_scaling"}],
        }
        fake_modifier_types = {"duration_scaling": {"is_district_duration": True}}

        with patch("calculations.field_calculations._resolve_def", return_value=fake_def), \
             patch.object(th, "json_data", {"modifier_types": fake_modifier_types}):
            result = th.district_duration_tick(old_nation, new_nation, {})

        counter = next(m for m in new_nation["modifiers"] if m["field"] == "district_sessions_sawmill")
        assert counter["value"] == 1
        assert "session count -> 1 (new)" in result
