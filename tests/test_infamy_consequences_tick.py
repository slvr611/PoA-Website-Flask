"""
Tests for helpers.tick_helpers.nation_infamy_consequences_tick.

Per the infamy design table's 100+ tier:
  - A nation ending a session at 100+ infamy is guaranteed a civil war next
    session. Civil wars in this codebase always need a human to pick the
    breakaway nation's name/territory/unit split (there's no algorithmic
    nation-splitter), so this is implemented as a "pending_civil_war" flag
    surfaced in the admin Civil War Helper, not an auto-executed split.
  - If that nation has vassals, ALL of them are forced into rebellion
    immediately (not the normal probabilistic rebellion_chance roll).
"""
from unittest.mock import patch, MagicMock
from bson import ObjectId

import helpers.tick_helpers as th


def _overlord(infamy, **overrides):
    doc = {"_id": ObjectId(), "name": "Overlord Nation", "infamy": infamy}
    doc.update(overrides)
    return doc


class TestInfamyConsequencesTick:
    def test_below_100_is_a_noop(self):
        old_nation = _overlord(99)
        new_nation = dict(old_nation)
        fake_db = MagicMock()
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            result = th.nation_infamy_consequences_tick(old_nation, new_nation, {})
        assert result == ""
        assert "pending_civil_war" not in new_nation
        fake_db.nations.find.assert_not_called()

    def test_100_plus_flags_pending_civil_war(self):
        old_nation = _overlord(100)
        new_nation = dict(old_nation)
        fake_db = MagicMock()
        fake_db.nations.find.return_value = []
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            result = th.nation_infamy_consequences_tick(old_nation, new_nation, {})
        assert new_nation["pending_civil_war"] is True
        assert "guaranteed a civil war" in result

    def test_already_flagged_nation_does_not_repeat_the_message(self):
        old_nation = _overlord(120, pending_civil_war=True)
        new_nation = dict(old_nation)
        fake_db = MagicMock()
        fake_db.nations.find.return_value = []
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            result = th.nation_infamy_consequences_tick(old_nation, new_nation, {})
        assert new_nation["pending_civil_war"] is True  # unchanged, not re-triggered
        assert "guaranteed a civil war" not in result

    def test_all_vassals_forced_into_rebellion(self):
        old_nation = _overlord(150)
        new_nation = dict(old_nation)
        vassal_a = {"_id": ObjectId(), "name": "Vassal A", "rebellion_chance": 0.1}
        vassal_b = {"_id": ObjectId(), "name": "Vassal B", "rebellion_chance": 0.2}
        fake_db = MagicMock()
        fake_db.nations.find.return_value = [vassal_a, vassal_b]

        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            result = th.nation_infamy_consequences_tick(old_nation, new_nation, {})

        assert "Vassal A has rebelled" in result
        assert "Vassal B has rebelled" in result
        # Each vassal's rebellion tracking fields must be directly force-set —
        # not left to the normal probabilistic rebellion_chance roll.
        update_calls = fake_db.nations.update_one.call_args_list
        assert len(update_calls) == 2
        for call in update_calls:
            args, kwargs = call
            filter_arg, update_arg = args
            assert filter_arg["_id"] in (vassal_a["_id"], vassal_b["_id"])
            assert update_arg["$set"]["rebellion_roll"] == 0.0

    def test_no_vassals_produces_no_rebellion_messages(self):
        old_nation = _overlord(100)
        new_nation = dict(old_nation)
        fake_db = MagicMock()
        fake_db.nations.find.return_value = []
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            result = th.nation_infamy_consequences_tick(old_nation, new_nation, {})
        assert "rebelled" not in result

    def test_queries_vassals_by_overlord_id(self):
        old_nation = _overlord(100)
        new_nation = dict(old_nation)
        fake_db = MagicMock()
        fake_db.nations.find.return_value = []
        with patch("helpers.tick_helpers.mongo", MagicMock(db=fake_db)):
            th.nation_infamy_consequences_tick(old_nation, new_nation, {})
        fake_db.nations.find.assert_called_once()
        call_filter = fake_db.nations.find.call_args[0][0]
        assert call_filter == {"overlord": str(old_nation["_id"])}
