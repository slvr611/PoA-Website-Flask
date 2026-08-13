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
# system_approve_change: two tick-queued changes to the same entity
# ---------------------------------------------------------------------------

class TestSameEntityDoubleQueuedInOneTick:
    """Production regression, and the defense-in-depth fallback under it.

    In normal tick flow, _commit_pending_changes now runs every data-type
    group through _merge_pending_by_entity first (see TestMergePendingByEntity
    below), so two queued changes to the same entity are combined into one
    before system_approve_change ever sees them — this class's scenarios
    don't actually arise from a real tick() /era_tick() run anymore.

    These tests call system_request_change/system_approve_change directly,
    bypassing that merge, to pin the *lower-level* fallback behavior on its
    own: a character death queues a cross-cutting nation update (e.g. adds a
    stability-loss modifier entry) AND the same tick's main per-nation loop
    separately queues "Tick Update for X" for that same nation, from an
    independent, earlier-taken snapshot. Once the first commits, the
    second's before_data no longer matches the live document (it doesn't
    know about the modifier the first one added) — check_no_other_changes
    correctly flags that as a divergence, but it's expected same-tick
    sequencing, not an outside edit, and previously aborted the whole batch
    with "could not be applied". This bypass stays in place as a safety net
    for anything that reaches system_approve_change without having gone
    through the merge (e.g. a future direct caller)."""

    def test_second_update_is_not_blocked_by_the_first_ones_side_effect(self, mock_mongo, fake_category_data):
        mock_mongo.db.players.insert_one({"name": "System"})
        nation_id = ObjectId()
        mock_mongo.db.nations.insert_one({"_id": nation_id, "name": "Test Nation", "money": 100, "modifiers": []})
        original = mock_mongo.db.nations.find_one({"_id": nation_id})

        before1 = dict(original)
        after1 = dict(original)
        after1["modifiers"] = [{"_id": "death-modifier", "name": "Stability loss", "value": -1}]

        before2 = dict(original)  # stale — taken before item 1 committed
        after2 = dict(original)
        after2["money"] = 105

        with patch("helpers.change_helpers.mongo", mock_mongo), \
             patch("helpers.change_helpers.category_data", fake_category_data), \
             patch("helpers.change_helpers._calculate_and_attach_fields", side_effect=lambda dt, d: d), \
             patch("helpers.change_helpers.propagate_updates"):
            cid1 = ch.system_request_change(
                data_type="nations", item_id=nation_id, change_type="Update",
                before_data=before1, after_data=after1, reason="Death of X caused nation update",
            )
            ok1 = ch.system_approve_change(cid1, skip_recalculation=True)

            cid2 = ch.system_request_change(
                data_type="nations", item_id=nation_id, change_type="Update",
                before_data=before2, after_data=after2, reason="Tick Update for X",
            )
            ok2 = ch.system_approve_change(cid2, skip_recalculation=True)

        assert ok1 is True
        assert ok2 is True, "second tick-queued update to the same entity must not be blocked by the first's side effect"
        final = mock_mongo.db.nations.find_one({"_id": nation_id})
        assert final["modifiers"] == [{"_id": "death-modifier", "name": "Stability loss", "value": -1}]
        assert final["money"] == 105

    def test_interactive_path_without_skip_recalculation_still_blocks_on_divergence(self, mock_mongo, fake_category_data):
        """The bypass only applies to tick-driven (skip_recalculation=True)
        commits — the admin/interactive approval flow must keep the
        conflict-detection safety net. Uses a plain string field (not a list)
        so the scenario is unambiguous: the target diverges from both what
        was requested (a rename) and what the caller last saw, with nothing
        list-merge-shaped to complicate it."""
        mock_mongo.db.players.insert_one({"name": "System"})
        nation_id = ObjectId()
        mock_mongo.db.nations.insert_one({"_id": nation_id, "name": "Original Name", "money": 100})
        original = mock_mongo.db.nations.find_one({"_id": nation_id})

        before1 = dict(original)
        after1 = dict(original)
        after1["name"] = "Renamed By Someone Else"
        before2 = dict(original)  # stale — still expects "Original Name"
        after2 = dict(original)
        after2["name"] = "Intended New Name"

        with patch("helpers.change_helpers.mongo", mock_mongo), \
             patch("helpers.change_helpers.category_data", fake_category_data), \
             patch("helpers.change_helpers._calculate_and_attach_fields", side_effect=lambda dt, d: d), \
             patch("helpers.change_helpers.propagate_updates"):
            cid1 = ch.system_request_change(
                data_type="nations", item_id=nation_id, change_type="Update",
                before_data=before1, after_data=after1, reason="first",
            )
            ch.system_approve_change(cid1, skip_recalculation=True)

            cid2 = ch.system_request_change(
                data_type="nations", item_id=nation_id, change_type="Update",
                before_data=before2, after_data=after2, reason="second",
            )
            ok2 = ch.system_approve_change(cid2)  # skip_recalculation defaults to False

        assert ok2 is False

    def test_fallback_path_same_list_field_touched_by_both_is_last_write_wins(self, mock_mongo, fake_category_data):
        """Pins the fallback path's OWN narrower limitation, now superseded
        in normal tick flow by _merge_pending_by_entity (see
        TestMergePendingByEntity, which asserts the *opposite* outcome —
        both sides' list edits correctly combined — for the equivalent
        scenario run through the real merge). Calling system_approve_change
        directly, bypassing the merge: if two changes to the same entity
        BOTH genuinely modify the same ID-keyed list field with different
        content, deep_merge's list-replace-when-both-sides-have-ids
        behavior means the second one's version silently wins — the first
        one's addition is lost, no error raised. Pinned so a future change
        to deep_merge's list semantics doesn't silently change this
        fallback-path behavior without it being noticed."""
        mock_mongo.db.players.insert_one({"name": "System"})
        nation_id = ObjectId()
        mock_mongo.db.nations.insert_one({
            "_id": nation_id, "name": "Test Nation",
            "modifiers": [{"_id": "old-mod", "name": "Old", "value": 1}],
        })
        original = mock_mongo.db.nations.find_one({"_id": nation_id})

        before1 = dict(original)
        after1 = dict(original)
        after1["modifiers"] = [
            {"_id": "old-mod", "name": "Old", "value": 1},
            {"_id": "death-mod", "name": "Death", "value": -1},
        ]
        before2 = dict(original)
        after2 = dict(original)
        after2["modifiers"] = []  # item2's own, independent intent: old-mod expired

        with patch("helpers.change_helpers.mongo", mock_mongo), \
             patch("helpers.change_helpers.category_data", fake_category_data), \
             patch("helpers.change_helpers._calculate_and_attach_fields", side_effect=lambda dt, d: d), \
             patch("helpers.change_helpers.propagate_updates"):
            cid1 = ch.system_request_change(
                data_type="nations", item_id=nation_id, change_type="Update",
                before_data=before1, after_data=after1, reason="death event",
            )
            ch.system_approve_change(cid1, skip_recalculation=True)

            cid2 = ch.system_request_change(
                data_type="nations", item_id=nation_id, change_type="Update",
                before_data=before2, after_data=after2, reason="regular tick, expires old-mod",
            )
            ok2 = ch.system_approve_change(cid2, skip_recalculation=True)

        assert ok2 is True
        final = mock_mongo.db.nations.find_one({"_id": nation_id})
        assert final["modifiers"] == []  # death-mod silently lost — known trade-off, not a goal


# ---------------------------------------------------------------------------
# _merge_pending_by_entity / _merge_after_data / _merge_id_keyed_lists:
# the single channel every tick-driven write goes through
# ---------------------------------------------------------------------------

class TestMergeIdKeyedLists:
    def test_addition_from_one_side_is_kept(self):
        base = [{"_id": "a", "v": 1}]
        a = [{"_id": "a", "v": 1}, {"_id": "b", "v": 2}]  # a added "b"
        b = [{"_id": "a", "v": 1}]  # b didn't touch it
        assert th._merge_id_keyed_lists(base, a, b) == [{"_id": "a", "v": 1}, {"_id": "b", "v": 2}]

    def test_additions_from_both_sides_are_both_kept(self):
        base = []
        a = [{"_id": "a", "v": 1}]
        b = [{"_id": "b", "v": 2}]
        result = th._merge_id_keyed_lists(base, a, b)
        assert {i["_id"] for i in result} == {"a", "b"}

    def test_removal_by_one_side_is_respected_when_other_side_unchanged(self):
        base = [{"_id": "a", "v": 1}]
        a = []  # a removed it
        b = [{"_id": "a", "v": 1}]  # b left it untouched
        assert th._merge_id_keyed_lists(base, a, b) == []

    def test_edit_by_one_side_wins_over_unrelated_removal_by_the_other(self):
        base = [{"_id": "a", "v": 1}]
        a = [{"_id": "a", "v": 99}]  # a edited it
        b = []  # b removed it — but a's edit is a real conflict, a's edit wins
        assert th._merge_id_keyed_lists(base, a, b) == [{"_id": "a", "v": 99}]

    def test_both_sides_remove_the_same_item(self):
        base = [{"_id": "a", "v": 1}]
        assert th._merge_id_keyed_lists(base, [], []) == []

    def test_empty_result_list_from_a_removal_does_not_break_downstream_merges(self):
        """Regression: an earlier version of this merge used a helper that
        treated an empty list as "not ID-keyed," which made a full removal
        by one side fall through to whole-list replacement and silently
        wipe out the OTHER side's addition. See _looks_id_keyed."""
        base = [{"_id": "old", "v": 1}]
        a = [{"_id": "old", "v": 1}, {"_id": "new", "v": 2}]  # a adds "new"
        b = []  # b removes "old" entirely — b's list is empty
        assert th._merge_id_keyed_lists(base, a, b) == [{"_id": "new", "v": 2}]


class TestLooksIdKeyed:
    def test_empty_list_is_id_keyed(self):
        assert th._looks_id_keyed([]) is True

    def test_list_of_dicts_with_ids_is_id_keyed(self):
        assert th._looks_id_keyed([{"_id": "a"}, {"_id": "b"}]) is True

    def test_list_with_a_dict_missing_id_is_not_id_keyed(self):
        assert th._looks_id_keyed([{"_id": "a"}, {"name": "no id"}]) is False

    def test_list_of_non_dicts_is_not_id_keyed(self):
        assert th._looks_id_keyed([1, 2, 3]) is False


class TestMergeAfterData:
    def test_only_one_side_changed_key_uses_that_sides_value(self):
        base = {"money": 100, "name": "N"}
        a = {"money": 105, "name": "N"}
        b = {"money": 100, "name": "N"}
        assert th._merge_after_data(base, a, b) == {"money": 105, "name": "N"}

    def test_both_sides_agree_keeps_the_value(self):
        base = {"money": 100}
        a = {"money": 105}
        b = {"money": 105}
        assert th._merge_after_data(base, a, b) == {"money": 105}

    def test_scalar_conflict_later_side_wins(self):
        base = {"name": "Original"}
        a = {"name": "Renamed by A"}
        b = {"name": "Renamed by B"}
        assert th._merge_after_data(base, a, b)["name"] == "Renamed by B"

    def test_id_keyed_list_conflict_is_combined_not_replaced(self):
        base = {"modifiers": [{"_id": "old", "v": 1}]}
        a = {"modifiers": [{"_id": "old", "v": 1}, {"_id": "death", "v": -1}]}
        b = {"modifiers": []}
        result = th._merge_after_data(base, a, b)
        assert result["modifiers"] == [{"_id": "death", "v": -1}]

    def test_keys_untouched_by_either_side_are_left_alone(self):
        base = {"money": 100, "stability": 5}
        a = {"money": 105, "stability": 5}
        b = {"money": 100, "stability": 5}
        assert th._merge_after_data(base, a, b) == {"money": 105, "stability": 5}


class TestMergePendingByEntity:
    def test_single_item_passes_through_unchanged(self):
        item = {"item_id": ObjectId(), "data_type": "nations", "change_type": "Update",
                "before_data": {}, "after_data": {"money": 5}, "reason": "r", "already_calculated": True}
        assert th._merge_pending_by_entity([item]) == [item]

    def test_different_entities_stay_separate(self):
        id1, id2 = ObjectId(), ObjectId()
        items = [
            {"item_id": id1, "data_type": "nations", "change_type": "Update",
             "before_data": {}, "after_data": {"money": 5}, "reason": "r1", "already_calculated": True},
            {"item_id": id2, "data_type": "nations", "change_type": "Update",
             "before_data": {}, "after_data": {"money": 5}, "reason": "r2", "already_calculated": True},
        ]
        result = th._merge_pending_by_entity(items)
        assert len(result) == 2
        assert {str(r["item_id"]) for r in result} == {str(id1), str(id2)}

    def test_two_items_for_the_same_entity_produce_one_merged_change(self):
        """The core production scenario: a character death queues a
        cross-cutting nation update (adds a modifier) and the main
        per-nation loop separately queues its own "Tick Update for X" for
        that same nation — both should collapse into exactly one change,
        with both sides' intent preserved."""
        nid = ObjectId()
        base = {"_id": nid, "name": "Test Nation", "money": 100, "modifiers": [{"_id": "old", "v": 1}]}
        item1 = {
            "item_id": nid, "data_type": "nations", "change_type": "Update",
            "before_data": dict(base),
            "after_data": {**base, "modifiers": [{"_id": "old", "v": 1}, {"_id": "death", "v": -1}]},
            "reason": "Death of X caused nation update", "already_calculated": True,
        }
        item2 = {
            "item_id": nid, "data_type": "nations", "change_type": "Update",
            "before_data": dict(base),
            "after_data": {**base, "money": 105, "modifiers": []},
            "reason": "Tick Update for X", "already_calculated": True,
        }

        result = th._merge_pending_by_entity([item1, item2])

        assert len(result) == 1
        merged = result[0]
        assert merged["reason"] == "Death of X caused nation update; Tick Update for X"
        assert merged["after_data"]["money"] == 105
        assert merged["after_data"]["modifiers"] == [{"_id": "death", "v": -1}]

    def test_already_calculated_is_false_if_any_contributor_is_false(self):
        nid = ObjectId()
        item1 = {"item_id": nid, "data_type": "nations", "change_type": "Update",
                 "before_data": {}, "after_data": {"a": 1}, "reason": "r1", "already_calculated": True}
        item2 = {"item_id": nid, "data_type": "nations", "change_type": "Update",
                 "before_data": {}, "after_data": {"a": 1}, "reason": "r2", "already_calculated": False}
        result = th._merge_pending_by_entity([item1, item2])
        assert result[0]["already_calculated"] is False

    def test_preserves_queue_order_for_first_appearance(self):
        id1, id2 = ObjectId(), ObjectId()
        items = [
            {"item_id": id2, "data_type": "nations", "change_type": "Update",
             "before_data": {}, "after_data": {}, "reason": "second-entity-first", "already_calculated": True},
            {"item_id": id1, "data_type": "nations", "change_type": "Update",
             "before_data": {}, "after_data": {}, "reason": "first-entity-second", "already_calculated": True},
        ]
        result = th._merge_pending_by_entity(items)
        assert [str(r["item_id"]) for r in result] == [str(id2), str(id1)]


class TestCommitPendingChangesMergesDuplicates:
    """Integration test: _commit_pending_changes (the real entry point tick()
    and era_tick() call) produces exactly one change document for an entity
    queued twice in the same tick, via the merge above — not two, and not a
    crash. mongomock doesn't support real transactions, so this exercises
    the grouping/merging/dispatch logic; the transactional commit itself was
    verified separately against the real Atlas connection."""

    def test_entity_queued_twice_commits_as_one_change_document(self, mock_mongo, fake_category_data):
        mock_mongo.db.players.insert_one({"name": "System"})
        nid = ObjectId()
        mock_mongo.db.nations.insert_one({
            "_id": nid, "name": "Test Nation", "money": 100,
            "modifiers": [{"_id": "old", "v": 1}],
        })
        mock_mongo.db.global_modifiers.insert_one({"name": "global_modifiers", "session_counter": 1})
        original = mock_mongo.db.nations.find_one({"_id": nid})

        pending = [
            {
                "data_type": "nations", "item_id": nid, "change_type": "Update",
                "before_data": dict(original),
                "after_data": {**original, "modifiers": [{"_id": "old", "v": 1}, {"_id": "death", "v": -1}]},
                "reason": "Death of X caused nation update", "already_calculated": True,
            },
            {
                "data_type": "nations", "item_id": nid, "change_type": "Update",
                "before_data": dict(original),
                "after_data": {**original, "money": 105, "modifiers": []},
                "reason": "Tick Update for X", "already_calculated": True,
            },
        ]

        class _FakeSession:
            # mongomock doesn't support real sessions — pass None through so
            # the underlying find/insert/update calls (session=None) work
            # against mongomock exactly like any non-transactional call.
            def with_transaction(self, callback):
                return callback(None)

        with patch("helpers.tick_helpers.mongo", mock_mongo), \
             patch("helpers.change_helpers.mongo", mock_mongo), \
             patch("helpers.change_helpers.category_data", fake_category_data), \
             patch("helpers.change_helpers._calculate_and_attach_fields", side_effect=lambda dt, d: d), \
             patch("helpers.change_helpers.propagate_updates"), \
             patch.object(mock_mongo, "cx", MagicMock(start_session=MagicMock(
                 return_value=MagicMock(__enter__=lambda s: _FakeSession(), __exit__=lambda *a: False)
             ))):
            th._commit_pending_changes(pending)

        changes = list(mock_mongo.db.changes.find({"target": nid}))
        assert len(changes) == 1, "two queued changes to the same entity must commit as one change document"
        final = mock_mongo.db.nations.find_one({"_id": nid})
        assert final["money"] == 105
        assert final["modifiers"] == [{"_id": "death", "v": -1}]


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
