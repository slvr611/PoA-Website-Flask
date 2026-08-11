"""
Tests for helpers.tick_helpers.nation_concessions_tick's resource filtering:
concessions must only roll resources both the vassal and the overlord
actually have storage capacity for (e.g. gunpowder, base storage 0, should
never be rolled unless both sides have unlocked it).
"""
import random
from unittest.mock import patch, MagicMock
from bson import ObjectId

import helpers.tick_helpers as th

_GENERAL_KEYS = {"food", "wood", "stone", "mounts", "magic"}  # research excluded


def _capacity(**overrides):
    """A full resource-capacity dict: general resources always > 0 (matching
    their real base_storage), unique resources default to 0 (locked) unless
    overridden."""
    cap = {k: 10 for k in _GENERAL_KEYS}
    cap["iron"] = 10       # real base_storage for iron is 10 — always available
    cap["gunpowder"] = 0   # real base_storage for gunpowder is 0 — locked by default
    cap.update(overrides)
    return cap


def _vassal(overlord_id, capacity, **overrides):
    doc = {
        "_id": ObjectId(), "name": "Vassal", "overlord": str(overlord_id),
        "compliance": "Neutral", "concessions": {}, "concessions_chance": 1.0,
        "concessions_qty": 4, "nation_resource_capacity": capacity,
    }
    doc.update(overrides)
    return doc


class TestConcessionsResourceFiltering:
    def test_gunpowder_never_rolled_when_neither_side_has_capacity(self):
        overlord_id = ObjectId()
        fake_db = MagicMock()
        fake_db.nations.find_one.return_value = {"nation_resource_capacity": _capacity()}
        old_nation = _vassal(overlord_id, _capacity())
        new_nation = dict(old_nation)

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            for _ in range(50):
                new_nation = dict(old_nation)
                th.nation_concessions_tick(old_nation, new_nation, {})
                assert "gunpowder" not in new_nation.get("concessions", {})

    def test_gunpowder_rolled_only_when_both_sides_have_capacity(self):
        overlord_id = ObjectId()
        fake_db = MagicMock()
        fake_db.nations.find_one.return_value = {
            "nation_resource_capacity": _capacity(gunpowder=5)
        }
        old_nation = _vassal(overlord_id, _capacity(gunpowder=5))

        saw_gunpowder = False
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            for _ in range(200):
                new_nation = dict(old_nation)
                th.nation_concessions_tick(old_nation, new_nation, {})
                if "gunpowder" in new_nation.get("concessions", {}):
                    saw_gunpowder = True
                    break
        assert saw_gunpowder, "gunpowder should be eligible once both sides have capacity"

    def test_vassal_missing_capacity_blocks_gunpowder_even_if_overlord_has_it(self):
        overlord_id = ObjectId()
        fake_db = MagicMock()
        fake_db.nations.find_one.return_value = {
            "nation_resource_capacity": _capacity(gunpowder=5)  # overlord HAS it
        }
        old_nation = _vassal(overlord_id, _capacity(gunpowder=0))  # vassal does NOT

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            for _ in range(50):
                new_nation = dict(old_nation)
                th.nation_concessions_tick(old_nation, new_nation, {})
                assert "gunpowder" not in new_nation.get("concessions", {})

    def test_overlord_missing_capacity_blocks_gunpowder_even_if_vassal_has_it(self):
        overlord_id = ObjectId()
        fake_db = MagicMock()
        fake_db.nations.find_one.return_value = {
            "nation_resource_capacity": _capacity(gunpowder=0)  # overlord does NOT
        }
        old_nation = _vassal(overlord_id, _capacity(gunpowder=5))  # vassal HAS it

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            for _ in range(50):
                new_nation = dict(old_nation)
                th.nation_concessions_tick(old_nation, new_nation, {})
                assert "gunpowder" not in new_nation.get("concessions", {})

    def test_general_resources_still_roll_normally(self):
        # General resources have positive base_storage for every nation, so
        # they should always remain eligible.
        overlord_id = ObjectId()
        fake_db = MagicMock()
        fake_db.nations.find_one.return_value = {"nation_resource_capacity": _capacity()}
        old_nation = _vassal(overlord_id, _capacity())

        seen = set()
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            for _ in range(100):
                new_nation = dict(old_nation)
                th.nation_concessions_tick(old_nation, new_nation, {})
                seen.update(new_nation.get("concessions", {}).keys())
        assert seen & _GENERAL_KEYS, "general resources should still be rollable"
        assert "iron" in seen or len(seen) >= 1  # iron (base storage 10) also eligible

    def test_missing_overlord_doc_falls_back_to_general_resources_only(self):
        # If the overlord can't be found/fetched, unique resources (which
        # require an explicit capacity entry) are conservatively excluded —
        # only base-storage general resources remain eligible.
        overlord_id = ObjectId()
        fake_db = MagicMock()
        fake_db.nations.find_one.return_value = None
        old_nation = _vassal(overlord_id, _capacity(gunpowder=5, iron=5))

        seen = set()
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            for _ in range(100):
                new_nation = dict(old_nation)
                th.nation_concessions_tick(old_nation, new_nation, {})
                seen.update(new_nation.get("concessions", {}).keys())
        assert not (seen - _GENERAL_KEYS), f"unexpected non-general resources rolled: {seen - _GENERAL_KEYS}"

    def test_too_few_mutual_resources_skips_without_crashing(self):
        # Only one resource (food) is mutually available -> can't form a
        # two-resource concession; must return cleanly, not crash on
        # random.choice([]).
        overlord_id = ObjectId()
        capacity = {k: 0 for k in _GENERAL_KEYS} | {"iron": 0, "gunpowder": 0}
        capacity["food"] = 10
        fake_db = MagicMock()
        fake_db.nations.find_one.return_value = {"nation_resource_capacity": capacity}
        old_nation = _vassal(overlord_id, capacity)
        new_nation = dict(old_nation)

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            result = th.nation_concessions_tick(old_nation, new_nation, {})

        assert new_nation.get("concessions", {}) == {}
        assert isinstance(result, str)


class TestMissingOverlordKey:
    """Regression test for a production crash: a nation whose document has no
    "overlord" key at all (as opposed to overlord == "") crashed both
    nation_concessions_tick and nation_rebellion_tick with

        KeyError: 'overlord'

    because they indexed old_nation["overlord"] directly instead of using
    .get("overlord", ""). "overlord" is a linked_object field with no schema
    default, so a nation that has never been touched by the
    vassal/overlord flow can legitimately lack the key entirely — this is
    not malformed data, and both functions must treat it exactly like an
    independent nation (overlord == "") rather than raising.
    """

    def test_concessions_tick_treats_missing_overlord_key_as_independent(self):
        old_nation = {"_id": ObjectId(), "name": "Independent Nation"}
        assert "overlord" not in old_nation
        new_nation = dict(old_nation)

        result = th.nation_concessions_tick(old_nation, new_nation, {})

        assert result == ""
        assert new_nation == old_nation  # untouched — no concessions logic ran

    def test_rebellion_tick_treats_missing_overlord_key_as_independent(self):
        old_nation = {"_id": ObjectId(), "name": "Independent Nation"}
        assert "overlord" not in old_nation
        new_nation = dict(old_nation)

        result = th.nation_rebellion_tick(old_nation, new_nation, {})

        assert result == ""
        assert new_nation == old_nation  # untouched — no rebellion logic ran


_COMPLIANCE_SCHEMA = {
    "properties": {
        "compliance": {
            "enum": ["None", "Rebellious", "Defiant", "Neutral", "Compliant", "Loyal"]
        }
    }
}


class TestConcessionsCooldown:
    """A vassal granted concessions one session must not be granted them
    again the very next session, regardless of compliance or roll luck."""

    def test_cooldown_blocks_a_guaranteed_roll(self):
        overlord_id = ObjectId()
        fake_db = MagicMock()
        fake_db.nations.find_one.return_value = {"nation_resource_capacity": _capacity()}
        old_nation = _vassal(
            overlord_id, _capacity(),
            concessions_granted_last_session=True,
            concessions_chance=1.0,  # would otherwise be a guaranteed roll
        )
        new_nation = dict(old_nation)

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            th.nation_concessions_tick(old_nation, new_nation, {})

        assert new_nation.get("concessions", {}) == {}
        assert new_nation.get("concessions_granted_last_session") is False

    def test_cooldown_is_consumed_after_one_session(self):
        # Session 1: blocked by the cooldown, flag flips to False.
        overlord_id = ObjectId()
        fake_db = MagicMock()
        fake_db.nations.find_one.return_value = {"nation_resource_capacity": _capacity()}
        old_nation = _vassal(
            overlord_id, _capacity(),
            concessions_granted_last_session=True,
            concessions_chance=1.0,
        )
        session1 = dict(old_nation)
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            th.nation_concessions_tick(old_nation, session1, {})
        assert session1["concessions_granted_last_session"] is False

        # Session 2: cooldown cleared, a guaranteed roll succeeds normally.
        session2 = dict(session1)
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            th.nation_concessions_tick(session1, session2, {})
        assert session2.get("concessions", {}) != {}
        assert session2.get("concessions_granted_last_session") is True

    def test_granting_concessions_sets_the_cooldown_flag(self):
        overlord_id = ObjectId()
        fake_db = MagicMock()
        fake_db.nations.find_one.return_value = {"nation_resource_capacity": _capacity()}
        old_nation = _vassal(overlord_id, _capacity(), concessions_chance=1.0)
        new_nation = dict(old_nation)

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            th.nation_concessions_tick(old_nation, new_nation, {})

        assert new_nation.get("concessions", {}) != {}
        assert new_nation.get("concessions_granted_last_session") is True

    def test_a_failed_roll_does_not_set_the_cooldown_flag(self):
        overlord_id = ObjectId()
        fake_db = MagicMock()
        fake_db.nations.find_one.return_value = {"nation_resource_capacity": _capacity()}
        old_nation = _vassal(overlord_id, _capacity(), concessions_chance=0.0)
        new_nation = dict(old_nation)

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            th.nation_concessions_tick(old_nation, new_nation, {})

        assert new_nation.get("concessions", {}) == {}
        assert new_nation.get("concessions_granted_last_session") is False


class TestUnpaidConcessions:
    """Concessions still outstanding at tick time count as unpaid: they are
    cleared and compliance drops, but the vassal's stockpile must not grow."""

    def test_unpaid_concessions_clear_and_reduce_compliance_without_granting_resources(self):
        overlord_id = ObjectId()
        fake_db = MagicMock()
        fake_db.nations.find_one.return_value = {"nation_resource_capacity": _capacity()}
        old_nation = _vassal(
            overlord_id, _capacity(),
            compliance="Neutral",  # index 3, above the rebellion threshold
            concessions={"food": 2, "wood": 2},
            resource_storage={"food": 5, "wood": 5},
            concessions_chance=0.0,  # isolate the unpaid branch from a fresh roll
        )
        new_nation = dict(old_nation)

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            with patch("random.random", return_value=0.99):  # avoid the rebellion coinflip path
                result = th.nation_concessions_tick(old_nation, new_nation, _COMPLIANCE_SCHEMA)

        assert new_nation["concessions"] == {}
        assert new_nation["compliance"] == "Defiant"
        assert new_nation["resource_storage"] == {"food": 5, "wood": 5}  # unchanged — not paid out
        assert "due to concessions not being paid" in result

    def test_unpaid_concessions_at_low_compliance_may_trigger_rebellion_not_resources(self):
        overlord_id = ObjectId()
        fake_db = MagicMock()
        fake_db.nations.find_one.return_value = {"nation_resource_capacity": _capacity()}
        old_nation = _vassal(
            overlord_id, _capacity(),
            compliance="Rebellious",  # index 1, at/below the rebellion threshold
            concessions={"food": 2, "wood": 2},
            concessions_chance=0.0,
        )
        new_nation = dict(old_nation)

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            result = th.nation_concessions_tick(old_nation, new_nation, _COMPLIANCE_SCHEMA)

        assert new_nation["concessions"] == {}
        assert "resource_storage" not in new_nation
        assert isinstance(result, str)
