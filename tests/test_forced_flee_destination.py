"""
Tests for the "Forced Flee Destination" region modifier (helpers/tick_helpers.py:
_forced_flee_destination_for_region, pop_flee_tick).

A region modifier of modifier_type "forced_flee_destination" with
target_value=<nation name> overrides pop_flee_tick's normal "random non-Closed
nation in the same region" destination pick — pops fleeing from any nation in
that region always go to the named nation instead. This is a hard override:
it bypasses the Closed-citizenship_stance filter entirely, since the whole
point is to redirect fleeing pops somewhere specific regardless of that
nation's normal openness to migrants.
"""
from unittest.mock import patch, MagicMock
from bson import ObjectId

import helpers.tick_helpers as th


def _region_with_modifier(target_value=None, region_id=None):
    modifiers = []
    if target_value is not None:
        modifiers.append({"modifier_type": "forced_flee_destination", "value": 1, "target_value": target_value})
    return {"_id": region_id or ObjectId(), "modifiers": modifiers}


class TestForcedFleeDestinationForRegion:
    def test_no_modifier_returns_none(self):
        region_id = ObjectId()
        fake_db = MagicMock()
        fake_db.regions.find_one.return_value = _region_with_modifier(region_id=region_id)
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            result = th._forced_flee_destination_for_region(str(region_id))
        assert result is None
        fake_db.nations.find_one.assert_not_called()

    def test_modifier_present_resolves_named_nation(self):
        region_id = ObjectId()
        dest_nation = {"_id": ObjectId(), "name": "Haven"}
        fake_db = MagicMock()
        fake_db.regions.find_one.return_value = _region_with_modifier("Haven", region_id)
        fake_db.nations.find_one.return_value = dest_nation
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            result = th._forced_flee_destination_for_region(str(region_id))
        assert result == dest_nation
        fake_db.nations.find_one.assert_called_once_with({"name": "Haven"}, {"_id": 1, "name": 1, "temperament": 1})

    def test_named_nation_not_found_returns_none(self):
        region_id = ObjectId()
        fake_db = MagicMock()
        fake_db.regions.find_one.return_value = _region_with_modifier("Nonexistent Nation", region_id)
        fake_db.nations.find_one.return_value = None
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            result = th._forced_flee_destination_for_region(str(region_id))
        assert result is None

    def test_region_not_found_returns_none(self):
        fake_db = MagicMock()
        fake_db.regions.find_one.return_value = None
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            result = th._forced_flee_destination_for_region(str(ObjectId()))
        assert result is None

    def test_other_modifier_types_are_ignored(self):
        region_id = ObjectId()
        fake_db = MagicMock()
        fake_db.regions.find_one.return_value = {
            "_id": region_id,
            "modifiers": [{"modifier_type": "stability_gain_chance", "value": 0.1}],
        }
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            result = th._forced_flee_destination_for_region(str(region_id))
        assert result is None


def _nation(region_id, **overrides):
    doc = {
        "_id": ObjectId(), "name": "Overcrowded Nation", "region": str(region_id),
        "pop_count": 20, "effective_pop_capacity": 10, "pop_flee_chance": 1.0,
    }
    doc.update(overrides)
    return doc


class TestPopFleeTickWithForcedDestination:
    def test_forced_destination_used_instead_of_random_candidate(self):
        region_id = ObjectId()
        old_nation = _nation(region_id)
        new_nation = dict(old_nation)
        dest_nation = {"_id": ObjectId(), "name": "Haven"}
        closed_nation = {"_id": ObjectId(), "name": "Closed Nation"}  # would normally be excluded anyway

        fake_db = MagicMock()
        fake_db.regions.find_one.return_value = _region_with_modifier("Haven", region_id)
        fake_db.nations.find_one.return_value = dest_nation
        fake_db.nations.find.return_value = [closed_nation]  # should never even be consulted
        fake_db.pops.find.return_value = [{"_id": ObjectId(), "race": "r", "culture": "c", "religion": "rel"}]

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)), \
             patch("helpers.tick_helpers.system_request_change", return_value="change123"), \
             patch("helpers.tick_helpers.system_approve_change", return_value=True):
            result = th.pop_flee_tick(old_nation, new_nation, {})

        assert "Haven" in result
        fake_db.nations.find.assert_not_called()  # never falls back to the random-candidate query

    def test_forced_destination_equal_to_source_nation_falls_back_to_random(self):
        """A region shouldn't be able to force a nation's own pops to flee to
        itself — that must fall back to the normal random-candidate pool."""
        region_id = ObjectId()
        old_nation = _nation(region_id)
        new_nation = dict(old_nation)
        other_candidate = {"_id": ObjectId(), "name": "Other Nation"}

        fake_db = MagicMock()
        # The "forced destination" resolves to the SAME nation that's overcrowded.
        fake_db.regions.find_one.return_value = _region_with_modifier(old_nation["name"], region_id)
        fake_db.nations.find_one.return_value = {"_id": old_nation["_id"], "name": old_nation["name"]}
        fake_db.nations.find.return_value = [other_candidate]
        fake_db.pops.find.return_value = [{"_id": ObjectId(), "race": "r", "culture": "c", "religion": "rel"}]

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)), \
             patch("helpers.tick_helpers.system_request_change", return_value="change123"), \
             patch("helpers.tick_helpers.system_approve_change", return_value=True):
            result = th.pop_flee_tick(old_nation, new_nation, {})

        assert "Other Nation" in result

    def test_no_forced_destination_uses_normal_random_candidate(self):
        region_id = ObjectId()
        old_nation = _nation(region_id)
        new_nation = dict(old_nation)
        candidate = {"_id": ObjectId(), "name": "Neighbor Nation"}

        fake_db = MagicMock()
        fake_db.regions.find_one.return_value = _region_with_modifier(region_id=region_id)  # no modifier
        fake_db.nations.find.return_value = [candidate]
        fake_db.pops.find.return_value = [{"_id": ObjectId(), "race": "r", "culture": "c", "religion": "rel"}]

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)), \
             patch("helpers.tick_helpers.system_request_change", return_value="change123"), \
             patch("helpers.tick_helpers.system_approve_change", return_value=True):
            result = th.pop_flee_tick(old_nation, new_nation, {})

        assert "Neighbor Nation" in result
        fake_db.nations.find.assert_called_once()

    def test_destination_variable_not_clobbered_by_stray_random_choice(self):
        """Regression guard: an earlier version of this function had a
        leftover `destination = random.choice(candidates)` AFTER the forced-
        destination logic, silently discarding it. The forced destination
        must be what's actually used in the resulting pop update."""
        region_id = ObjectId()
        old_nation = _nation(region_id)
        new_nation = dict(old_nation)
        dest_nation = {"_id": ObjectId(), "name": "Haven"}
        decoy_candidate = {"_id": ObjectId(), "name": "Decoy Nation"}

        fake_db = MagicMock()
        fake_db.regions.find_one.return_value = _region_with_modifier("Haven", region_id)
        fake_db.nations.find_one.return_value = dest_nation
        fake_db.nations.find.return_value = [decoy_candidate]
        fake_db.pops.find.return_value = [{"_id": ObjectId(), "race": "r", "culture": "c", "religion": "rel"}]

        captured = {}
        def _capture_request_change(**kwargs):
            captured.update(kwargs)
            return "change123"

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)), \
             patch("helpers.tick_helpers.system_request_change", side_effect=_capture_request_change), \
             patch("helpers.tick_helpers.system_approve_change", return_value=True):
            th.pop_flee_tick(old_nation, new_nation, {})

        assert captured["after_data"]["nation"] == str(dest_nation["_id"])
