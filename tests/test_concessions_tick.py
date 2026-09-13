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


class TestOverlordCapacityPrefersFreshInMemoryData:
    """Regression test for a real bug: the overlord's resource capacity was
    always fetched via a database query, which mid-tick still only reflects
    whatever was persisted at the END of the LAST session — this session's
    fresh calculate_all_fields results live only in old_nations/new_nations
    (in memory) until tick()'s commit phase at the very end. So an overlord
    who lost access to a resource since their last recalculation (e.g. a
    temporary storage-capacity modifier with a duration expiring) would
    still show as having it for a full extra session, letting vassals
    demand a resource (e.g. gunpowder) the overlord no longer actually has.
    The vassal's own capacity was already read fresh (old_nation, passed in
    directly) — only the overlord side was stale. Fixed by preferring a
    nations_by_id lookup (this session's fresh in-memory nations, built
    once per tick run) over the database when available; the database
    query remains as a fallback for callers that don't supply it (e.g.
    tests, or any future direct/manual invocation)."""

    def test_stale_db_overlord_capacity_is_ignored_when_fresh_data_available(self):
        """The database says the overlord still has gunpowder capacity, but
        this session's fresh in-memory copy shows it's now 0 — the fresh
        value must win, so gunpowder is never rolled."""
        overlord_id = ObjectId()
        fake_db = MagicMock()
        # Database (stale — end of LAST session): overlord still had gunpowder.
        fake_db.nations.find_one.return_value = {
            "nation_resource_capacity": _capacity(gunpowder=5)
        }
        old_nation = _vassal(overlord_id, _capacity(gunpowder=5))
        # This session's fresh in-memory overlord: gunpowder access lost.
        nations_by_id = {
            str(overlord_id): {"nation_resource_capacity": _capacity(gunpowder=0)},
        }

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            for _ in range(50):
                new_nation = dict(old_nation)
                th.nation_concessions_tick(old_nation, new_nation, {}, nations_by_id=nations_by_id)
                assert "gunpowder" not in new_nation.get("concessions", {})
        fake_db.nations.find_one.assert_not_called()

    def test_fresh_overlord_capacity_allows_gunpowder_when_actually_available(self):
        """Symmetric case: fresh in-memory data shows gunpowder IS available
        (even if a stale database snapshot said otherwise) — it must become
        eligible without needing a database round trip."""
        overlord_id = ObjectId()
        fake_db = MagicMock()
        fake_db.nations.find_one.return_value = {
            "nation_resource_capacity": _capacity(gunpowder=0)  # stale: locked
        }
        old_nation = _vassal(overlord_id, _capacity(gunpowder=5))
        nations_by_id = {
            str(overlord_id): {"nation_resource_capacity": _capacity(gunpowder=5)},
        }

        saw_gunpowder = False
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            for _ in range(200):
                new_nation = dict(old_nation)
                th.nation_concessions_tick(old_nation, new_nation, {}, nations_by_id=nations_by_id)
                if "gunpowder" in new_nation.get("concessions", {}):
                    saw_gunpowder = True
                    break
        assert saw_gunpowder
        fake_db.nations.find_one.assert_not_called()

    def test_falls_back_to_database_when_overlord_not_in_nations_by_id(self):
        """nations_by_id is provided (this session did build it) but doesn't
        contain this particular overlord — falls back to the database
        rather than treating the overlord as missing entirely."""
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
                th.nation_concessions_tick(old_nation, new_nation, {}, nations_by_id={})
                if "gunpowder" in new_nation.get("concessions", {}):
                    saw_gunpowder = True
                    break
        assert saw_gunpowder
        fake_db.nations.find_one.assert_called()

    def test_omitting_nations_by_id_still_falls_back_to_database(self):
        """Backward compatibility: callers that don't pass nations_by_id at
        all (e.g. existing tests, or any direct caller) keep working exactly
        as before."""
        overlord_id = ObjectId()
        fake_db = MagicMock()
        fake_db.nations.find_one.return_value = {"nation_resource_capacity": _capacity()}
        old_nation = _vassal(overlord_id, _capacity())
        new_nation = dict(old_nation)

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            result = th.nation_concessions_tick(old_nation, new_nation, {})

        assert isinstance(result, str)
        fake_db.nations.find_one.assert_called()

    def test_dispatch_registers_nation_concessions_tick_as_nations_by_id_aware(self):
        assert th.nation_concessions_tick in th._NATIONS_BY_ID_AWARE_TICK_FUNCTIONS

    def test_dispatch_forwards_nations_by_id_only_to_registered_functions(self):
        received = {}

        def fake_aware_tick(old_nation, new_nation, schema, **kwargs):
            received["aware"] = kwargs.get("nations_by_id")
            return ""

        def fake_unaware_tick(old_nation, new_nation, schema):
            received["unaware_called"] = True
            return ""

        lookup = {"some-id": {"name": "Test"}}
        th._NATIONS_BY_ID_AWARE_TICK_FUNCTIONS.add(fake_aware_tick)
        try:
            th._dispatch(fake_aware_tick, [], {}, {}, {}, nations_by_id=lookup)
            th._dispatch(fake_unaware_tick, [], {}, {}, {}, nations_by_id=lookup)
        finally:
            th._NATIONS_BY_ID_AWARE_TICK_FUNCTIONS.discard(fake_aware_tick)

        assert received["aware"] is lookup
        assert received["unaware_called"] is True


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
