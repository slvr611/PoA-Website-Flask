"""
Tests for the tick system's atomic-commit machinery (helpers/tick_helpers.py's
_queue_change/_dispatch/_commit_pending_changes and the caller-supplied _id
support in helpers/change_helpers.py's system_request_change).

Context: tick()/era_tick() used to write to MongoDB immediately, one document
at a time, as each tick function ran — including several tick functions that
reach across data types mid-computation (a character's death updating their
nation, an artifact being lost, etc.). If the tick crashed partway through
(e.g. the KeyError: 'overlord' incident this work was prompted by), whatever
had already been written stayed written. The fix collects every change into
a `pending` list during a pure-computation phase, then commits all of them as
one MongoDB transaction — see _commit_pending_changes's docstring.

mongomock (used everywhere else in this suite) does not support real
transactions, so the tests here exercise the queueing/dispatch/ID-generation
logic in isolation rather than opening an actual multi-document transaction —
that guarantee was verified separately against the real Atlas connection.
"""
from unittest.mock import patch, MagicMock
from bson import ObjectId

import helpers.tick_helpers as th
import helpers.change_helpers as ch


# ---------------------------------------------------------------------------
# _queue_change
# ---------------------------------------------------------------------------

class TestQueueChange:
    def test_appends_to_pending_instead_of_committing(self):
        pending = []
        with patch("helpers.tick_helpers.system_request_change") as req, \
             patch("helpers.tick_helpers.system_approve_change") as approve:
            result = th._queue_change(
                pending, data_type="nations", item_id="abc", change_type="Update",
                before_data={"a": 1}, after_data={"a": 2}, reason="test",
            )
        req.assert_not_called()
        approve.assert_not_called()
        assert result is None
        assert pending == [{
            "data_type": "nations", "item_id": "abc", "change_type": "Update",
            "before_data": {"a": 1}, "after_data": {"a": 2}, "reason": "test",
            "already_calculated": False,
        }]

    def test_appends_already_calculated_flag_when_set(self):
        pending = []
        th._queue_change(
            pending, data_type="nations", item_id="abc", change_type="Update",
            before_data={"a": 1}, after_data={"a": 2}, reason="test",
            already_calculated=True,
        )
        assert pending[0]["already_calculated"] is True

    def test_pending_none_falls_back_to_immediate_commit(self):
        with patch("helpers.tick_helpers.system_request_change", return_value="change123") as req, \
             patch("helpers.tick_helpers.system_approve_change") as approve:
            result = th._queue_change(
                None, data_type="nations", item_id="abc", change_type="Update",
                before_data={"a": 1}, after_data={"a": 2}, reason="test",
            )
        req.assert_called_once_with(
            data_type="nations", item_id="abc", change_type="Update",
            before_data={"a": 1}, after_data={"a": 2}, reason="test",
        )
        approve.assert_called_once_with("change123", skip_recalculation=False)
        assert result == "change123"

    def test_pending_none_forwards_already_calculated_as_skip_recalculation(self):
        with patch("helpers.tick_helpers.system_request_change", return_value="change123"), \
             patch("helpers.tick_helpers.system_approve_change") as approve:
            th._queue_change(
                None, data_type="nations", item_id="abc", change_type="Update",
                before_data={"a": 1}, after_data={"a": 2}, reason="test",
                already_calculated=True,
            )
        approve.assert_called_once_with("change123", skip_recalculation=True)

    def test_pending_none_and_no_requester_skips_approve(self):
        """system_request_change returns None when the "System" player is
        missing (see its own guard clause) — must not then call
        system_approve_change(None)."""
        with patch("helpers.tick_helpers.system_request_change", return_value=None), \
             patch("helpers.tick_helpers.system_approve_change") as approve:
            result = th._queue_change(
                None, data_type="nations", item_id="abc", change_type="Update",
                before_data={}, after_data={}, reason="test",
            )
        approve.assert_not_called()
        assert result is None


# ---------------------------------------------------------------------------
# _dispatch
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_pending_aware_function_receives_pending_as_keyword(self):
        seen = {}

        def fake_tick(old, new, schema, pending=None):
            seen["pending"] = pending
            return "ok"

        with patch("helpers.tick_helpers._PENDING_AWARE_TICK_FUNCTIONS", {fake_tick}):
            result = th._dispatch(fake_tick, ["marker"], {"old": 1}, {"new": 1}, {})
        assert result == "ok"
        assert seen["pending"] == ["marker"]

    def test_non_pending_aware_function_called_unchanged(self):
        def fake_tick(old, new, schema):
            return "unchanged"

        # fake_tick is deliberately NOT in _PENDING_AWARE_TICK_FUNCTIONS.
        result = th._dispatch(fake_tick, ["marker"], {"old": 1}, {"new": 1}, {})
        assert result == "unchanged"

    def test_zero_arg_pending_aware_function(self):
        seen = {}

        def fake_zero_arg(pending=None):
            seen["pending"] = pending
            return "zero-arg ok"

        with patch("helpers.tick_helpers._PENDING_AWARE_TICK_FUNCTIONS", {fake_zero_arg}):
            result = th._dispatch(fake_zero_arg, ["marker"])
        assert result == "zero-arg ok"
        assert seen["pending"] == ["marker"]


# ---------------------------------------------------------------------------
# system_request_change: caller-supplied _id for Add changes
# ---------------------------------------------------------------------------

class TestCallerSuppliedAddId:
    def test_item_id_provided_for_add_is_stamped_onto_after_data(self, mock_mongo):
        mock_mongo.db.players.insert_one({"name": "System"})
        preset_id = ObjectId()
        with patch("helpers.change_helpers.mongo", mock_mongo):
            change_id = ch.system_request_change(
                data_type="characters", item_id=preset_id, change_type="Add",
                before_data={}, after_data={"name": "Test Ruler"}, reason="test",
            )
        change = mock_mongo.db.changes.find_one({"_id": change_id})
        assert change["after_requested_data"]["_id"] == preset_id
        assert change["target"] == preset_id

    def test_item_id_none_for_add_leaves_id_unset(self, mock_mongo):
        mock_mongo.db.players.insert_one({"name": "System"})
        with patch("helpers.change_helpers.mongo", mock_mongo):
            change_id = ch.system_request_change(
                data_type="characters", item_id=None, change_type="Add",
                before_data={}, after_data={"name": "Test Ruler"}, reason="test",
            )
        change = mock_mongo.db.changes.find_one({"_id": change_id})
        assert "_id" not in change["after_requested_data"]

    def test_add_with_preset_id_inserts_with_that_exact_id(self, mock_mongo, fake_category_data):
        mock_mongo.db.players.insert_one({"name": "System"})
        preset_id = ObjectId()
        with patch("helpers.change_helpers.mongo", mock_mongo), \
             patch("helpers.change_helpers.category_data", fake_category_data), \
             patch("helpers.change_helpers._calculate_and_attach_fields", side_effect=lambda dt, d: d), \
             patch("helpers.change_helpers.propagate_updates"):
            change_id = ch.system_request_change(
                data_type="characters", item_id=preset_id, change_type="Add",
                before_data={}, after_data={"name": "Test Ruler"}, reason="test",
            )
            ok = ch.system_approve_change(change_id)
        assert ok
        stored = mock_mongo.db.characters.find_one({"_id": preset_id})
        assert stored is not None
        assert stored["name"] == "Test Ruler"


# ---------------------------------------------------------------------------
# generate_ai_character: no read-back needed for the new character's id
# ---------------------------------------------------------------------------

_CHARACTER_SCHEMA = {
    "properties": {
        "positive_quirk": {"enum": ["None", "Brave"]},
        "negative_quirk": {"enum": ["None", "Greedy"]},
    }
}


class TestGenerateAiCharacterDeferred:
    def test_artifact_reassignment_uses_pregenerated_id_without_readback(self, mock_mongo):
        """The predecessor's artifact update must reference the new
        character's id even though the character Add itself is only queued
        (not actually inserted) — proving the id no longer depends on a
        mongo.db.changes read-back after an immediate insert."""
        org = {"_id": ObjectId(), "name": "Test Nation", "_calc_cache": {}}
        previous_leader = {"_id": ObjectId(), "name": "Old Ruler"}
        artifact = {"_id": ObjectId(), "owner": str(previous_leader["_id"]), "archived": False}

        mock_mongo.db.characters.find_one = MagicMock(return_value=None)  # name is unique
        mock_mongo.db.artifacts.find = MagicMock(return_value=[artifact])
        mock_mongo.db.pops.find = MagicMock(return_value=[])

        pending = []
        with patch("helpers.tick_helpers.mongo", mock_mongo):
            th.generate_ai_character(org, {}, _CHARACTER_SCHEMA, previous_leader=previous_leader, pending=pending)

        add_entries = [p for p in pending if p["data_type"] == "characters" and p["change_type"] == "Add"]
        artifact_entries = [p for p in pending if p["data_type"] == "artifacts"]
        assert len(add_entries) == 1
        assert len(artifact_entries) == 1

        new_char_id = add_entries[0]["item_id"]
        assert new_char_id is not None
        assert add_entries[0]["after_data"]["_id"] == new_char_id
        assert artifact_entries[0]["after_data"]["owner"] == str(new_char_id)
        assert artifact_entries[0]["item_id"] == artifact["_id"]

    def test_pending_none_falls_back_to_immediate_commit_per_step(self, mock_mongo):
        """Without a pending list (e.g. called directly, outside a tick run),
        behavior should match the old immediate-commit-per-step flow."""
        org = {"_id": ObjectId(), "name": "Test Nation", "_calc_cache": {}}

        mock_mongo.db.characters.find_one = MagicMock(return_value=None)
        mock_mongo.db.pops.find = MagicMock(return_value=[])

        with patch("helpers.tick_helpers.mongo", mock_mongo), \
             patch("helpers.tick_helpers.system_request_change", return_value=None) as req, \
             patch("helpers.tick_helpers.system_approve_change") as approve:
            th.generate_ai_character(org, {}, _CHARACTER_SCHEMA, pending=None)

        assert req.called
        # system_approve_change should never be called with None (no requester).
        for call in approve.call_args_list:
            assert call.args[0] is not None
