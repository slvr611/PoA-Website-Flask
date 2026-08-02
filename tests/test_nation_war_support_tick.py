"""
Tests for nation_war_support_tick (helpers/tick_helpers.py): a new
player/mod-editable 0-100 "war_support" nation stat that drops while at war
(-20 for fighting offensively, -10 for fighting defensively — both apply and
stack if the nation is doing both in different wars at once) and recovers
+10/session at peace, capped at 0-100.

Mirrors nation_infamy_decay_tick's existing pattern for determining live war
participation (war_links joined against wars, checked against
session_declared/session_ended), just checked for both stances instead of
only "Attacker".
"""
from unittest.mock import patch, MagicMock
from bson import ObjectId

import mongomock

import helpers.tick_helpers as th


def _make_nation(war_support=100, nation_id=None):
    return {"_id": nation_id or ObjectId(), "name": "Testland", "war_support": war_support}


class TestNationWarSupportTick:
    def _run(self, test_db, old_nation, current_session=5):
        test_db["global_modifiers"].insert_one({"name": "global_modifiers", "session_counter": current_session})
        fake_mongo = MagicMock()
        fake_mongo.db = test_db
        new_nation = dict(old_nation)
        with patch.object(th, "mongo", fake_mongo):
            th.nation_war_support_tick(old_nation, new_nation, {})
        return new_nation

    def test_no_war_increases_by_10(self):
        client = mongomock.MongoClient()
        test_db = client["test"]
        old_nation = _make_nation(war_support=50)

        new_nation = self._run(test_db, old_nation)

        assert new_nation["war_support"] == 60

    def test_no_war_caps_at_100(self):
        client = mongomock.MongoClient()
        test_db = client["test"]
        old_nation = _make_nation(war_support=95)

        new_nation = self._run(test_db, old_nation)

        assert new_nation["war_support"] == 100

    def test_offensive_war_decreases_by_20(self):
        client = mongomock.MongoClient()
        test_db = client["test"]
        old_nation = _make_nation(war_support=50)
        war_id = ObjectId()
        test_db["wars"].insert_one({"_id": war_id, "session_declared": 1, "session_ended": None})
        test_db["war_links"].insert_one({"war": str(war_id), "participant": str(old_nation["_id"]), "stance": "Attacker"})

        new_nation = self._run(test_db, old_nation)

        assert new_nation["war_support"] == 30

    def test_defensive_war_decreases_by_10(self):
        client = mongomock.MongoClient()
        test_db = client["test"]
        old_nation = _make_nation(war_support=50)
        war_id = ObjectId()
        test_db["wars"].insert_one({"_id": war_id, "session_declared": 1, "session_ended": None})
        test_db["war_links"].insert_one({"war": str(war_id), "participant": str(old_nation["_id"]), "stance": "Defender"})

        new_nation = self._run(test_db, old_nation)

        assert new_nation["war_support"] == 40

    def test_offensive_and_defensive_wars_stack(self):
        client = mongomock.MongoClient()
        test_db = client["test"]
        old_nation = _make_nation(war_support=50)
        war_a, war_b = ObjectId(), ObjectId()
        test_db["wars"].insert_one({"_id": war_a, "session_declared": 1, "session_ended": None})
        test_db["wars"].insert_one({"_id": war_b, "session_declared": 1, "session_ended": None})
        test_db["war_links"].insert_one({"war": str(war_a), "participant": str(old_nation["_id"]), "stance": "Attacker"})
        test_db["war_links"].insert_one({"war": str(war_b), "participant": str(old_nation["_id"]), "stance": "Defender"})

        new_nation = self._run(test_db, old_nation)

        assert new_nation["war_support"] == 20  # 50 - 20 - 10

    def test_war_support_does_not_go_below_zero(self):
        client = mongomock.MongoClient()
        test_db = client["test"]
        old_nation = _make_nation(war_support=5)
        war_id = ObjectId()
        test_db["wars"].insert_one({"_id": war_id, "session_declared": 1, "session_ended": None})
        test_db["war_links"].insert_one({"war": str(war_id), "participant": str(old_nation["_id"]), "stance": "Attacker"})

        new_nation = self._run(test_db, old_nation)

        assert new_nation["war_support"] == 0

    def test_ended_war_does_not_count_as_active(self):
        client = mongomock.MongoClient()
        test_db = client["test"]
        old_nation = _make_nation(war_support=50)
        war_id = ObjectId()
        # Ended before the current session (5).
        test_db["wars"].insert_one({"_id": war_id, "session_declared": 1, "session_ended": 3})
        test_db["war_links"].insert_one({"war": str(war_id), "participant": str(old_nation["_id"]), "stance": "Attacker"})

        new_nation = self._run(test_db, old_nation, current_session=5)

        assert new_nation["war_support"] == 60  # treated as being at peace

    def test_not_yet_declared_war_does_not_count_as_active(self):
        client = mongomock.MongoClient()
        test_db = client["test"]
        old_nation = _make_nation(war_support=50)
        war_id = ObjectId()
        # Declared after the current session (5) — shouldn't happen in
        # practice, but the same guard nation_infamy_decay_tick uses.
        test_db["wars"].insert_one({"_id": war_id, "session_declared": 10, "session_ended": None})
        test_db["war_links"].insert_one({"war": str(war_id), "participant": str(old_nation["_id"]), "stance": "Attacker"})

        new_nation = self._run(test_db, old_nation, current_session=5)

        assert new_nation["war_support"] == 60

    def test_defaults_to_100_when_field_missing(self):
        client = mongomock.MongoClient()
        test_db = client["test"]
        old_nation = {"_id": ObjectId(), "name": "Testland"}  # no war_support key at all

        new_nation = self._run(test_db, old_nation)

        assert new_nation["war_support"] == 100  # 100 default, +10 clamped at 100
