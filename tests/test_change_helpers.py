"""
Tests for helpers/change_helpers.py.

Structure
─────────
Section 1 – Pure function tests
    TestDeepMerge
    TestDeepCompare
    TestKeepOnlyDifferences
    TestCalculateIntChanges
    TestCheckNoOtherChanges

Section 2 – Integration tests  (mongomock + Flask app context)
    Fixtures: patch_helpers, db_with_players, nation_id
    TestRequestChange
    TestSystemRequestChange
    TestApproveChange
    TestSystemApproveChange
    TestDenyChange

All integration tests patch the following inside change_helpers so they run
against an isolated in-memory database and never touch real MongoDB:
  • ``mongo``                     → mock_mongo  (backed by mongomock)
  • ``category_data``             → fake_category_data
  • ``_calculate_and_attach_fields`` → identity function (returns object unchanged)
  • ``propagate_updates``         → no-op

Flask's ``g`` is populated inside each test via ``flask_app.test_request_context``.
"""
import pytest
from unittest.mock import patch
from bson import ObjectId
from copy import deepcopy
from datetime import datetime, timezone

import helpers.change_helpers as ch


# ============================================================================
# Section 1 — Pure function tests
# ============================================================================

class TestDeepMerge:
    """helpers.change_helpers.deep_merge"""

    def test_flat_merge_overwrites_existing_value(self):
        result = ch.deep_merge({"a": 1, "b": 2}, {"b": 99})
        assert result == {"a": 1, "b": 99}

    def test_flat_merge_adds_new_key(self):
        result = ch.deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_nested_dict_merges_recursively(self):
        original = {"stats": {"hp": 10, "mp": 5}}
        updates  = {"stats": {"hp": 20}}
        result = ch.deep_merge(original, updates)
        # hp updated, mp preserved
        assert result == {"stats": {"hp": 20, "mp": 5}}

    def test_empty_dict_in_updates_wipes_nested_dict(self):
        original = {"stats": {"hp": 10, "mp": 5}}
        updates  = {"stats": {}}
        result = ch.deep_merge(original, updates)
        assert result == {"stats": {}}

    def test_list_replaced_positionally(self):
        # Lists are not deep-merged; the update list wins element-by-element
        original = {"items": [1, 2, 3]}
        updates  = {"items": [10, 20]}
        result = ch.deep_merge(original, updates)
        assert result == {"items": [10, 20]}

    def test_list_of_dicts_merged_positionally(self):
        # deep_merge iterates over the *updates* list, so the result is
        # truncated to its length — elements beyond updates are dropped.
        original = {"troops": [{"name": "A", "count": 5}, {"name": "B", "count": 3}]}
        updates  = {"troops": [{"count": 10}]}
        result = ch.deep_merge(original, updates)
        assert len(result["troops"]) == 1          # second element dropped
        assert result["troops"][0] == {"name": "A", "count": 10}  # first merged

    def test_does_not_mutate_original(self):
        original = {"a": {"x": 1}}
        snapshot = deepcopy(original)
        ch.deep_merge(original, {"a": {"x": 99}})
        assert original == snapshot

    def test_top_level_scalar_overwrite(self):
        assert ch.deep_merge({"v": "old"}, {"v": "new"}) == {"v": "new"}

    def test_id_based_replacement_requires_both_sides_to_have_ids(self):
        # When only the UPDATE list has _id but the EXISTING list does not,
        # deep_merge must NOT do a full list replacement.  Full replacement
        # would wipe all data on existing items, replacing them with minimal
        # {"_id": "hex"} dicts (the bug that wiped progress quest data).
        # Instead it should fall back to positional merge so existing data
        # is preserved and the new item is appended.
        existing = {
            "progress_quests": [
                {
                    "quest_name": "Existing Quest",
                    "current_progress": 6,
                    "required_progress": 19,
                    "slot": "no_slot",
                },
                {},
            ]
        }
        # Simulates after_data where _ensure_item_ids added _id to every item:
        # the first two quests are represented only by their new _id (unchanged
        # fields were stripped by keep_only_differences), and the third is new.
        after_data = {
            "progress_quests": [
                {"_id": "aaa1"},
                {"_id": "aaa2"},
                {
                    "_id": "aaa3",
                    "quest_name": "New Quest",
                    "current_progress": 0,
                    "required_progress": 10,
                    "slot": "no_slot",
                },
            ]
        }
        result = ch.deep_merge(existing, after_data)

        # Existing quest data must NOT be wiped
        assert result["progress_quests"][0].get("quest_name") == "Existing Quest", \
            "Existing quest data was wiped by deep_merge"
        assert result["progress_quests"][0].get("current_progress") == 6, \
            "current_progress was reset"
        # New quest must be appended
        assert len(result["progress_quests"]) == 3
        assert result["progress_quests"][2].get("quest_name") == "New Quest"

    def test_id_based_full_replacement_when_both_sides_have_ids(self):
        # When BOTH existing and update lists carry _id, full ID-based
        # replacement is correct (the after list is the authoritative state).
        existing = {
            "quests": [
                {"_id": "x1", "quest_name": "Old Quest", "current_progress": 5},
                {"_id": "x2", "quest_name": "Another", "current_progress": 3},
            ]
        }
        after_data = {
            "quests": [
                {"_id": "x1", "quest_name": "Old Quest", "current_progress": 7},
                {"_id": "x2", "quest_name": "Another", "current_progress": 3},
                {"_id": "x3", "quest_name": "Brand New", "current_progress": 0},
            ]
        }
        result = ch.deep_merge(existing, after_data)
        assert len(result["quests"]) == 3
        assert result["quests"][0]["current_progress"] == 7
        assert result["quests"][2]["quest_name"] == "Brand New"


class TestDeepCompare:
    """helpers.change_helpers.deep_compare"""

    def test_equal_flat_dicts(self):
        assert ch.deep_compare({"a": 1, "b": 2}, {"a": 1, "b": 2}) is True

    def test_different_values(self):
        assert ch.deep_compare({"a": 1}, {"a": 2}) is False

    def test_different_keys(self):
        assert ch.deep_compare({"a": 1}, {"b": 1}) is False

    def test_one_extra_key(self):
        assert ch.deep_compare({"a": 1}, {"a": 1, "b": 2}) is False

    def test_equal_nested_dicts(self):
        assert ch.deep_compare({"a": {"b": 1}}, {"a": {"b": 1}}) is True

    def test_different_nested_value(self):
        assert ch.deep_compare({"a": {"b": 1}}, {"a": {"b": 2}}) is False

    def test_equal_lists(self):
        assert ch.deep_compare([1, 2, 3], [1, 2, 3]) is True

    def test_different_list_values(self):
        assert ch.deep_compare([1, 2, 3], [1, 2, 4]) is False

    def test_different_list_lengths(self):
        assert ch.deep_compare([1, 2], [1, 2, 3]) is False

    def test_empty_dicts_are_equal(self):
        assert ch.deep_compare({}, {}) is True

    def test_equal_primitive(self):
        assert ch.deep_compare(42, 42) is True

    def test_different_primitive(self):
        assert ch.deep_compare(42, 43) is False


class TestKeepOnlyDifferences:
    """helpers.change_helpers.keep_only_differences (and its dict/list helpers)"""

    def test_no_differences_returns_empty_dicts(self):
        before = {"name": "A", "gold": 10}
        after  = {"name": "A", "gold": 10}
        new_before, new_after = ch.keep_only_differences(before, after, "Update")
        assert new_before == {}
        assert new_after  == {}

    def test_single_value_change(self):
        before = {"name": "Old", "gold": 10}
        after  = {"name": "New", "gold": 10}
        new_before, new_after = ch.keep_only_differences(before, after, "Update")
        assert new_before == {"name": "Old"}
        assert new_after  == {"name": "New"}

    def test_unchanged_fields_excluded(self):
        before = {"a": 1, "b": 2, "c": 3}
        after  = {"a": 1, "b": 99, "c": 3}
        new_before, new_after = ch.keep_only_differences(before, after, "Update")
        assert "a" not in new_before
        assert "c" not in new_before
        assert new_before == {"b": 2}
        assert new_after  == {"b": 99}

    def test_nested_dict_change(self):
        before = {"stats": {"hp": 10, "mp": 5}}
        after  = {"stats": {"hp": 20, "mp": 5}}
        new_before, new_after = ch.keep_only_differences(before, after, "Update")
        assert new_before == {"stats": {"hp": 10}}
        assert new_after  == {"stats": {"hp": 20}}

    def test_nested_dict_entirely_unchanged_excluded(self):
        before = {"stats": {"hp": 10}, "name": "A"}
        after  = {"stats": {"hp": 10}, "name": "B"}
        new_before, new_after = ch.keep_only_differences(before, after, "Update")
        assert "stats" not in new_before
        assert new_before == {"name": "A"}

    def test_list_change_included(self):
        before = {"tags": ["x", "y"]}
        after  = {"tags": ["x", "z"]}
        new_before, new_after = ch.keep_only_differences(before, after, "Update")
        assert "tags" in new_before
        assert "tags" in new_after

    def test_list_unchanged_excluded(self):
        before = {"tags": ["x", "y"]}
        after  = {"tags": ["x", "y"]}
        new_before, new_after = ch.keep_only_differences(before, after, "Update")
        assert "tags" not in new_before
        assert "tags" not in new_after

    def test_remove_type_maps_all_values_to_none(self):
        before = {"name": "A", "gold": 10}
        after  = {}
        new_before, new_after = ch.keep_only_differences(before, after, "Remove")
        assert new_before == {"name": "A", "gold": 10}
        assert new_after  == {"name": None, "gold": None}

    def test_add_type_new_doc_returns_all_after_fields(self):
        before = {}
        after  = {"name": "NewNation", "gold": 0}
        new_before, new_after = ch.keep_only_differences(before, after, "Add")
        assert new_after == {"name": "NewNation", "gold": 0}

    def test_empty_after_dict_with_non_empty_before_returns_empty(self):
        # When after_data is empty it means the form submitted nothing for this
        # sub-dict (e.g. a NavalUnitAssignmentDict with no registered fields).
        # There are no tracked keys to diff, so both sides return empty — no change.
        before = {"x": 1}
        after  = {}
        new_before, new_after = ch.keep_only_differences_dict(before, after)
        assert new_before == {}
        assert new_after  == {}


class TestCalculateIntChanges:
    """helpers.change_helpers.calculate_int_changes"""

    def test_positive_delta(self):
        assert ch.calculate_int_changes({"gold": 10}, {"gold": 25}) == {"gold": 15}

    def test_negative_delta(self):
        assert ch.calculate_int_changes({"troops": 100}, {"troops": 70}) == {"troops": -30}

    def test_zero_delta_included(self):
        assert ch.calculate_int_changes({"gold": 10}, {"gold": 10}) == {"gold": 0}

    def test_non_int_fields_excluded(self):
        result = ch.calculate_int_changes({"name": "A", "gold": 5}, {"name": "B", "gold": 10})
        assert "name" not in result
        assert result == {"gold": 5}

    def test_key_only_in_before_excluded(self):
        # silver only in before, gold only in after — neither has both int values
        result = ch.calculate_int_changes({"silver": 5}, {"gold": 10})
        assert result == {}

    def test_multiple_fields(self):
        before = {"gold": 5, "wood": 10, "food": 3}
        after  = {"gold": 15, "wood": 8,  "food": 3}
        result = ch.calculate_int_changes(before, after)
        assert result == {"gold": 10, "wood": -2, "food": 0}

    def test_mixed_types_only_ints_included(self):
        before = {"score": 100, "label": "old"}
        after  = {"score": 200, "label": "new"}
        result = ch.calculate_int_changes(before, after)
        assert result == {"score": 100}
        assert "label" not in result


class TestCheckNoOtherChanges:
    """helpers.change_helpers.check_no_other_changes"""

    def test_current_matches_before_returns_true(self):
        # Nothing has changed in the DB since the request was made
        before  = {"name": "OldName"}
        after   = {"name": "NewName"}
        current = {"name": "OldName", "unrelated": "ignored"}
        assert ch.check_no_other_changes(before, after, current) is True

    def test_current_matches_after_returns_true(self):
        # The change was already applied (perhaps by another process)
        before  = {"name": "OldName"}
        after   = {"name": "NewName"}
        current = {"name": "NewName"}
        assert ch.check_no_other_changes(before, after, current) is True

    def test_external_non_int_change_returns_false(self):
        # A third party changed the field to something neither before nor after
        before  = {"name": "OldName"}
        after   = {"name": "NewName"}
        current = {"name": "SomethingElse"}
        assert ch.check_no_other_changes(before, after, current) is False

    def test_external_int_change_is_allowed(self):
        # Integer fields are explicitly skipped — numeric drift is tolerated
        before  = {"gold": 10}
        after   = {"gold": 20}
        current = {"gold": 15}   # changed externally, but int → allowed
        assert ch.check_no_other_changes(before, after, current) is True

    def test_key_only_in_current_is_ignored(self):
        # Fields not mentioned in after_data are skipped entirely
        before  = {"name": "A"}
        after   = {"name": "B"}
        current = {"name": "A", "extra_field": "externally_added"}
        assert ch.check_no_other_changes(before, after, current) is True

    def test_nested_dict_external_change_returns_false(self):
        # The int exemption applies recursively, so use a string field to
        # trigger the False path for a genuine external non-int change.
        before  = {"stats": {"stance": "defensive"}}
        after   = {"stats": {"stance": "aggressive"}}
        current = {"stats": {"stance": "neutral"}}   # neither before nor after, non-int
        assert ch.check_no_other_changes(before, after, current) is False

    def test_nested_int_external_change_is_allowed(self):
        # The integer exemption applies at every nesting level
        before  = {"stats": {"hp": 10}}
        after   = {"stats": {"hp": 20}}
        current = {"stats": {"hp": 99}}   # int field → exempted even when nested
        assert ch.check_no_other_changes(before, after, current) is True

    def test_nested_dict_matches_before_returns_true(self):
        before  = {"stats": {"hp": 10}}
        after   = {"stats": {"hp": 20}}
        current = {"stats": {"hp": 10}}
        assert ch.check_no_other_changes(before, after, current) is True

    def test_list_same_length_matching_before_returns_true(self):
        before  = {"items": ["a", "b"]}
        after   = {"items": ["a", "c"]}
        current = {"items": ["a", "b"]}
        assert ch.check_no_other_changes(before, after, current) is True

    def test_list_wrong_length_and_neither_match_returns_false(self):
        before  = {"items": ["a", "b"]}
        after   = {"items": ["a", "c"]}
        current = {"items": ["a", "b", "d"]}  # different length from both
        assert ch.check_no_other_changes(before, after, current) is False

    def test_empty_dicts_return_true(self):
        assert ch.check_no_other_changes({}, {}, {}) is True


# ============================================================================
# Section 2 — Integration tests
# ============================================================================

# Sentinel Discord user IDs used across integration tests
_REGULAR_DISCORD_ID = "discord_user_regular"
_ADMIN_DISCORD_ID   = "discord_admin_user"


# ---------------------------------------------------------------------------
# Shared integration fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def patch_helpers(mock_mongo, fake_category_data):
    """Patch change_helpers to use the isolated in-memory database.

    Four things are patched:
    1. ``mongo``                      → mongomock-backed mock
    2. ``category_data``              → fake dict with mongomock collections
    3. ``_calculate_and_attach_fields`` → identity (avoids complex calculations)
    4. ``propagate_updates``          → no-op (avoids cascading DB queries)
    """
    with patch("helpers.change_helpers.mongo", mock_mongo), \
         patch("helpers.change_helpers.category_data", fake_category_data), \
         patch("helpers.change_helpers._calculate_and_attach_fields",
               side_effect=lambda data_type, obj: obj), \
         patch("helpers.change_helpers.propagate_updates", return_value=None):
        yield


@pytest.fixture
def db_with_players(test_db):
    """Seed the test DB with a regular player, an admin player, and a System player."""
    test_db["players"].insert_many([
        {"name": "RegularUser", "id": _REGULAR_DISCORD_ID, "is_admin": False},
        {"name": "AdminUser",   "id": _ADMIN_DISCORD_ID,   "is_admin": True},
        {"name": "System",                                   "is_admin": True},
    ])
    return test_db


@pytest.fixture
def nation_id(db_with_players):
    """Insert a test nation and return its ObjectId."""
    result = db_with_players["nations"].insert_one({
        "name": "TestNation",
        "gold": 100,
        "description": "A nation for testing",
    })
    return result.inserted_id


def _insert_pending_change(db, change_type, target_id, before, after,
                            target_collection="nations"):
    """Helper: insert a minimal Pending change document and return its _id."""
    doc = {
        "target_collection": target_collection,
        "target":            target_id,
        "change_type":       change_type,
        "before_requested_data": before,
        "after_requested_data":  after,
        "differential_data":     {},
        "request_reason":        "test",
        "status":                "Pending",
        "time_requested":        datetime.now(timezone.utc),
        "last_modified_time":    datetime.now(timezone.utc),
    }
    return db["changes"].insert_one(doc).inserted_id


# ---------------------------------------------------------------------------
# TestRequestChange
# ---------------------------------------------------------------------------

class TestRequestChange:
    """request_change() — creates a Pending change document."""

    def test_creates_pending_change_document(self, db_with_players, patch_helpers, flask_app):
        nation_id = db_with_players["nations"].insert_one({"name": "N"}).inserted_id
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _REGULAR_DISCORD_ID}
            change_id = ch.request_change(
                "nations", nation_id, "Update",
                {"name": "Old"}, {"name": "New"}, "Test reason"
            )

        assert change_id is not None
        change = db_with_players["changes"].find_one({"_id": change_id})
        assert change is not None
        assert change["status"]           == "Pending"
        assert change["change_type"]      == "Update"
        assert change["target_collection"] == "nations"
        assert change["target"]           == nation_id
        assert change["request_reason"]   == "Test reason"

    def test_requester_field_set_to_player_id(self, db_with_players, patch_helpers, flask_app):
        nation_id = db_with_players["nations"].insert_one({"name": "N"}).inserted_id
        player = db_with_players["players"].find_one({"id": _REGULAR_DISCORD_ID})
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _REGULAR_DISCORD_ID}
            change_id = ch.request_change(
                "nations", nation_id, "Update",
                {"name": "Old"}, {"name": "New"}, "reason"
            )

        change = db_with_players["changes"].find_one({"_id": change_id})
        assert change["requester"] == player["_id"]

    def test_strips_underscore_id_from_before_and_after(self, db_with_players, patch_helpers, flask_app):
        nation_id = db_with_players["nations"].insert_one({"name": "N"}).inserted_id
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _REGULAR_DISCORD_ID}
            change_id = ch.request_change(
                "nations", nation_id, "Update",
                {"_id": nation_id, "name": "Old"},
                {"_id": nation_id, "name": "New"},
                "strip _id test"
            )

        change = db_with_players["changes"].find_one({"_id": change_id})
        assert "_id" not in change["before_requested_data"]
        assert "_id" not in change["after_requested_data"]

    def test_strips_reason_field_from_after_data(self, db_with_players, patch_helpers, flask_app):
        nation_id = db_with_players["nations"].insert_one({"name": "N"}).inserted_id
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _REGULAR_DISCORD_ID}
            change_id = ch.request_change(
                "nations", nation_id, "Update",
                {"name": "Old"},
                {"name": "New", "reason": "stale form value"},
                "strip reason test"
            )

        change = db_with_players["changes"].find_one({"_id": change_id})
        assert "reason" not in change["after_requested_data"]

    def test_stores_only_differing_fields(self, db_with_players, patch_helpers, flask_app):
        nation_id = db_with_players["nations"].insert_one({"name": "N"}).inserted_id
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _REGULAR_DISCORD_ID}
            change_id = ch.request_change(
                "nations", nation_id, "Update",
                {"name": "Old", "gold": 100},
                {"name": "New", "gold": 100},   # gold unchanged
                "diff test"
            )

        change = db_with_players["changes"].find_one({"_id": change_id})
        # Only name changed; gold should not appear in the stored diff
        assert "gold" not in change["before_requested_data"]
        assert "gold" not in change["after_requested_data"]
        assert change["before_requested_data"] == {"name": "Old"}
        assert change["after_requested_data"]  == {"name": "New"}

    def test_computes_integer_differential(self, db_with_players, patch_helpers, flask_app):
        nation_id = db_with_players["nations"].insert_one({"name": "N"}).inserted_id
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _REGULAR_DISCORD_ID}
            change_id = ch.request_change(
                "nations", nation_id, "Update",
                {"gold": 50}, {"gold": 80}, "diff test"
            )

        change = db_with_players["changes"].find_one({"_id": change_id})
        assert change["differential_data"] == {"gold": 30}

    def test_raises_if_player_not_found(self, test_db, patch_helpers, flask_app):
        """DB has no players; find_one returns None → subscripting None raises TypeError."""
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": "ghost_user"}
            with pytest.raises(TypeError):
                ch.request_change(
                    "nations", ObjectId(), "Update",
                    {}, {"name": "X"}, "reason"
                )


# ---------------------------------------------------------------------------
# TestSystemRequestChange
# ---------------------------------------------------------------------------

class TestSystemRequestChange:
    """system_request_change() — like request_change but uses the System player."""

    def test_creates_pending_change(self, db_with_players, patch_helpers):
        nation_id = db_with_players["nations"].insert_one({"name": "N"}).inserted_id
        system    = db_with_players["players"].find_one({"name": "System"})

        change_id = ch.system_request_change(
            "nations", nation_id, "Update",
            {"name": "Old"}, {"name": "New"}, "system test"
        )

        assert change_id is not None
        change = db_with_players["changes"].find_one({"_id": change_id})
        assert change["status"]    == "Pending"
        assert change["requester"] == system

    def test_returns_none_when_system_player_missing(self, test_db, patch_helpers):
        """No players in DB → system_request_change returns None."""
        result = ch.system_request_change(
            "nations", ObjectId(), "Update",
            {}, {"name": "X"}, "no system player"
        )
        assert result is None


# ---------------------------------------------------------------------------
# TestApproveChange
# ---------------------------------------------------------------------------

class TestApproveChange:
    """approve_change() — applies Update / Add / Remove changes."""

    # ── Update ──────────────────────────────────────────────────────────────

    def test_update_modifies_target_in_db(self, db_with_players, nation_id, patch_helpers, flask_app):
        change_id = _insert_pending_change(
            db_with_players, "Update", nation_id,
            before={"name": "TestNation"},
            after ={"name": "RenamedNation"},
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            result = ch.approve_change(change_id)

        assert result is True
        updated = db_with_players["nations"].find_one({"_id": nation_id})
        assert updated["name"] == "RenamedNation"

    def test_update_preserves_unchanged_fields(self, db_with_players, nation_id, patch_helpers, flask_app):
        # The nation has gold=100 and description; we only change name.
        # gold and description should be preserved after approval.
        change_id = _insert_pending_change(
            db_with_players, "Update", nation_id,
            before={"name": "TestNation"},
            after ={"name": "RenamedNation"},
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            ch.approve_change(change_id)

        updated = db_with_players["nations"].find_one({"_id": nation_id})
        assert updated["gold"]        == 100
        assert updated["description"] == "A nation for testing"

    def test_update_sets_approved_status(self, db_with_players, nation_id, patch_helpers, flask_app):
        change_id = _insert_pending_change(
            db_with_players, "Update", nation_id,
            before={"name": "TestNation"},
            after ={"name": "X"},
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            ch.approve_change(change_id)

        change = db_with_players["changes"].find_one({"_id": change_id})
        assert change["status"]          == "Approved"
        assert "time_implemented"        in change
        assert "approver"                in change
        assert "last_modified_time"      in change

    def test_update_records_correct_approver(self, db_with_players, nation_id, patch_helpers, flask_app):
        admin     = db_with_players["players"].find_one({"id": _ADMIN_DISCORD_ID})
        change_id = _insert_pending_change(
            db_with_players, "Update", nation_id,
            before={"name": "TestNation"}, after={"name": "X"},
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            ch.approve_change(change_id)

        change = db_with_players["changes"].find_one({"_id": change_id})
        assert change["approver"] == admin["_id"]

    # ── Add ─────────────────────────────────────────────────────────────────

    def test_add_inserts_new_document(self, db_with_players, patch_helpers, flask_app):
        change_id = _insert_pending_change(
            db_with_players, "Add", target_id=None,
            before={}, after={"name": "BrandNewNation", "gold": 0},
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            result = ch.approve_change(change_id)

        assert result is True
        assert db_with_players["nations"].find_one({"name": "BrandNewNation"}) is not None

    def test_add_updates_change_target_with_inserted_id(self, db_with_players, patch_helpers, flask_app):
        change_id = _insert_pending_change(
            db_with_players, "Add", target_id=None,
            before={}, after={"name": "AnotherNation"},
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            ch.approve_change(change_id)

        change   = db_with_players["changes"].find_one({"_id": change_id})
        inserted = db_with_players["nations"].find_one({"_id": change["target"]})
        assert inserted is not None
        assert inserted["name"] == "AnotherNation"

    def test_add_sets_approved_status(self, db_with_players, patch_helpers, flask_app):
        change_id = _insert_pending_change(
            db_with_players, "Add", target_id=None,
            before={}, after={"name": "N"},
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            ch.approve_change(change_id)

        change = db_with_players["changes"].find_one({"_id": change_id})
        assert change["status"] == "Approved"

    # ── Remove ──────────────────────────────────────────────────────────────

    def test_remove_deletes_target_document(self, db_with_players, nation_id, patch_helpers, flask_app):
        change_id = _insert_pending_change(
            db_with_players, "Remove", nation_id,
            before={"name": "TestNation"}, after={"name": None},
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            result = ch.approve_change(change_id)

        assert result is True
        assert db_with_players["nations"].find_one({"_id": nation_id}) is None

    def test_remove_sets_approved_status(self, db_with_players, nation_id, patch_helpers, flask_app):
        change_id = _insert_pending_change(
            db_with_players, "Remove", nation_id,
            before={"name": "TestNation"}, after={"name": None},
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            ch.approve_change(change_id)

        change = db_with_players["changes"].find_one({"_id": change_id})
        assert change["status"] == "Approved"

    # ── Auth guard ──────────────────────────────────────────────────────────

    def test_non_admin_cannot_approve(self, db_with_players, nation_id, patch_helpers, flask_app):
        change_id = _insert_pending_change(
            db_with_players, "Update", nation_id,
            before={"name": "TestNation"}, after={"name": "X"},
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _REGULAR_DISCORD_ID}   # not admin
            result = ch.approve_change(change_id)

        assert result is None
        # Target and change status must be untouched
        nation = db_with_players["nations"].find_one({"_id": nation_id})
        assert nation["name"] == "TestNation"
        change = db_with_players["changes"].find_one({"_id": change_id})
        assert change["status"] == "Pending"

    # ── Conflict detection ───────────────────────────────────────────────────

    def test_returns_false_when_target_changed_externally(self, db_with_players, nation_id,
                                                           patch_helpers, flask_app):
        # Change was requested when name was "TestNation"
        change_id = _insert_pending_change(
            db_with_players, "Update", nation_id,
            before={"name": "TestNation"},
            after ={"name": "RenamedNation"},
        )
        # A third party modifies the name before approval
        db_with_players["nations"].update_one(
            {"_id": nation_id}, {"$set": {"name": "ChangedExternally"}}
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            result = ch.approve_change(change_id)

        assert result is False
        # Name should remain as the external value; change still Pending
        nation = db_with_players["nations"].find_one({"_id": nation_id})
        assert nation["name"] == "ChangedExternally"
        change = db_with_players["changes"].find_one({"_id": change_id})
        assert change["status"] == "Pending"

    def test_integer_field_external_drift_does_not_block_approval(self, db_with_players, nation_id,
                                                                    patch_helpers, flask_app):
        # gold changed externally (int drift is tolerated)
        change_id = _insert_pending_change(
            db_with_players, "Update", nation_id,
            before={"gold": 100},
            after ={"gold": 200},
        )
        db_with_players["nations"].update_one(
            {"_id": nation_id}, {"$set": {"gold": 150}}  # drifted externally
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            result = ch.approve_change(change_id)

        assert result is True


# ---------------------------------------------------------------------------
# TestSystemApproveChange
# ---------------------------------------------------------------------------

class TestSystemApproveChange:
    """system_approve_change() — same logic as approve_change but no g.user."""

    def test_update_modifies_target(self, db_with_players, patch_helpers):
        nation_id = db_with_players["nations"].insert_one(
            {"name": "SysNation", "gold": 10}
        ).inserted_id
        change_id = _insert_pending_change(
            db_with_players, "Update", nation_id,
            before={"name": "SysNation"},
            after ={"name": "SysNationRenamed"},
        )
        result = ch.system_approve_change(change_id)

        assert result is True
        updated = db_with_players["nations"].find_one({"_id": nation_id})
        assert updated["name"] == "SysNationRenamed"

    def test_add_inserts_new_document(self, db_with_players, patch_helpers):
        change_id = _insert_pending_change(
            db_with_players, "Add", target_id=None,
            before={}, after={"name": "SystemAdded"},
        )
        result = ch.system_approve_change(change_id)

        assert result is True
        assert db_with_players["nations"].find_one({"name": "SystemAdded"}) is not None

    def test_remove_deletes_target(self, db_with_players, patch_helpers):
        nation_id = db_with_players["nations"].insert_one({"name": "ToDelete"}).inserted_id
        change_id = _insert_pending_change(
            db_with_players, "Remove", nation_id,
            before={"name": "ToDelete"}, after={"name": None},
        )
        result = ch.system_approve_change(change_id)

        assert result is True
        assert db_with_players["nations"].find_one({"_id": nation_id}) is None

    def test_sets_approved_status_and_system_approver(self, db_with_players, patch_helpers):
        system    = db_with_players["players"].find_one({"name": "System"})
        nation_id = db_with_players["nations"].insert_one({"name": "N"}).inserted_id
        change_id = _insert_pending_change(
            db_with_players, "Update", nation_id,
            before={"name": "N"}, after={"name": "M"},
        )
        ch.system_approve_change(change_id)

        change = db_with_players["changes"].find_one({"_id": change_id})
        assert change["status"]   == "Approved"
        assert change["approver"] == system["_id"]


# ---------------------------------------------------------------------------
# TestDenyChange
# ---------------------------------------------------------------------------

class TestDenyChange:
    """deny_change() — rejects a Pending change."""

    def test_sets_rejected_status(self, db_with_players, nation_id, patch_helpers, flask_app):
        change_id = _insert_pending_change(
            db_with_players, "Update", nation_id,
            before={"name": "TestNation"}, after={"name": "X"},
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            result = ch.deny_change(change_id)

        assert result is True
        change = db_with_players["changes"].find_one({"_id": change_id})
        assert change["status"] == "Rejected"

    def test_records_denier_and_timestamp(self, db_with_players, nation_id, patch_helpers, flask_app):
        admin     = db_with_players["players"].find_one({"id": _ADMIN_DISCORD_ID})
        change_id = _insert_pending_change(
            db_with_players, "Update", nation_id,
            before={"name": "TestNation"}, after={"name": "X"},
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            ch.deny_change(change_id)

        change = db_with_players["changes"].find_one({"_id": change_id})
        assert change["denier"]       == admin["_id"]
        assert "time_rejected"        in change   # stored as time_rejected per current code
        assert "last_modified_time"   in change

    def test_non_admin_cannot_deny(self, db_with_players, nation_id, patch_helpers, flask_app):
        change_id = _insert_pending_change(
            db_with_players, "Update", nation_id,
            before={"name": "TestNation"}, after={"name": "X"},
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _REGULAR_DISCORD_ID}   # not admin
            result = ch.deny_change(change_id)

        assert result is False
        change = db_with_players["changes"].find_one({"_id": change_id})
        assert change["status"] == "Pending"

    def test_deny_does_not_modify_target(self, db_with_players, nation_id, patch_helpers, flask_app):
        change_id = _insert_pending_change(
            db_with_players, "Update", nation_id,
            before={"name": "TestNation"}, after={"name": "X"},
        )
        with flask_app.test_request_context("/"):
            from flask import g
            g.user = {"id": _ADMIN_DISCORD_ID}
            ch.deny_change(change_id)

        nation = db_with_players["nations"].find_one({"_id": nation_id})
        assert nation["name"] == "TestNation"   # unchanged


# ============================================================================
# Section 3 — ID-based array tracking tests
# ============================================================================

class TestEnsureItemIds:
    """helpers.change_helpers._ensure_item_ids"""

    def test_assigns_id_to_list_item_without_one(self):
        data = {"modifiers": [{"field": "hp", "value": 1}]}
        ch._ensure_item_ids(data)
        assert "_id" in data["modifiers"][0]
        assert isinstance(data["modifiers"][0]["_id"], str)

    def test_does_not_overwrite_existing_id(self):
        data = {"modifiers": [{"_id": "abc12345", "field": "hp"}]}
        ch._ensure_item_ids(data)
        assert data["modifiers"][0]["_id"] == "abc12345"

    def test_assigns_ids_to_multiple_items(self):
        data = {"modifiers": [{"field": "a"}, {"field": "b"}, {"field": "c"}]}
        ch._ensure_item_ids(data)
        ids = [item["_id"] for item in data["modifiers"]]
        assert len(ids) == 3
        assert len(set(ids)) == 3   # all unique

    def test_handles_nested_list_in_item(self):
        """Items inside nested lists also get IDs."""
        data = {"cities": [{"name": "Testopolis", "districts": [{"type": "port"}]}]}
        ch._ensure_item_ids(data)
        assert "_id" in data["cities"][0]
        assert "_id" in data["cities"][0]["districts"][0]

    def test_ignores_non_dict_list_items(self):
        data = {"tags": ["alpha", "beta"]}
        ch._ensure_item_ids(data)   # should not raise
        assert data["tags"] == ["alpha", "beta"]

    def test_handles_empty_list(self):
        data = {"modifiers": []}
        ch._ensure_item_ids(data)
        assert data["modifiers"] == []


class TestAllHaveIds:
    """helpers.change_helpers._all_have_ids"""

    def test_empty_list_returns_false(self):
        assert ch._all_have_ids([]) is False

    def test_list_with_no_ids_returns_false(self):
        assert ch._all_have_ids([{"field": "hp"}, {"field": "mp"}]) is False

    def test_partial_ids_returns_false(self):
        assert ch._all_have_ids([{"_id": "x", "field": "hp"}, {"field": "mp"}]) is False

    def test_all_with_ids_returns_true(self):
        assert ch._all_have_ids([{"_id": "x", "field": "hp"}, {"_id": "y", "field": "mp"}]) is True

    def test_non_dict_items_ignored(self):
        # A list of scalars — no dict items → False
        assert ch._all_have_ids(["alpha", "beta"]) is False

    def test_mixed_dict_and_scalar_returns_false(self):
        # Non-dict entries in the list cause the whole check to fail
        assert ch._all_have_ids([{"_id": "x"}, "scalar"]) is False


class TestKeepOnlyDifferencesListIdBased:
    """keep_only_differences_list uses ID-based matching when all items have _id."""

    def test_reorder_only_produces_different_lists(self):
        """A pure reorder yields the full lists (non-equal), capturing the reorder."""
        before = [{"_id": "x", "v": 1}, {"_id": "y", "v": 2}]
        after  = [{"_id": "y", "v": 2}, {"_id": "x", "v": 1}]
        rb, ra = ch.keep_only_differences_list(before, after)
        # The full lists are returned unchanged — they differ positionally
        assert rb == before
        assert ra == after

    def test_identical_lists_produce_equal_output(self):
        items = [{"_id": "x", "v": 1}, {"_id": "y", "v": 2}]
        rb, ra = ch.keep_only_differences_list(items, deepcopy(items))
        assert rb == ra   # deep_compare(rb, ra) will be True → field excluded

    def test_no_ids_falls_back_to_positional(self):
        before = [{"v": 1}, {"v": 2}]
        after  = [{"v": 1}, {"v": 99}]
        rb, ra = ch.keep_only_differences_list(before, after)
        # Positional: item 0 diff = ({}, {}), item 1 diff = ({v:2}, {v:99})
        assert rb[0] == {}
        assert ra[0] == {}
        assert rb[1] == {"v": 2}
        assert ra[1] == {"v": 99}


class TestDeepMergeIdBased:
    """deep_merge uses ID-based list replacement when items carry _id."""

    def test_merge_replaces_list_by_id_when_ids_present(self):
        original = {"mods": [{"_id": "x", "v": 1}, {"_id": "y", "v": 2}]}
        # after_data has the full reordered + modified list
        updates  = {"mods": [{"_id": "y", "v": 2}, {"_id": "x", "v": 99}]}
        result = ch.deep_merge(original, updates)
        assert result["mods"] == [{"_id": "y", "v": 2}, {"_id": "x", "v": 99}]

    def test_merge_reorder_only(self):
        original = {"mods": [{"_id": "a", "v": 10}, {"_id": "b", "v": 20}]}
        updates  = {"mods": [{"_id": "b", "v": 20}, {"_id": "a", "v": 10}]}
        result = ch.deep_merge(original, updates)
        assert result["mods"][0]["_id"] == "b"
        assert result["mods"][1]["_id"] == "a"

    def test_merge_positional_when_no_ids(self):
        original = {"mods": [{"v": 1}, {"v": 2}]}
        updates  = {"mods": [{"v": 99}]}
        result = ch.deep_merge(original, updates)
        # Positional: update list is shorter, so result is truncated to update length
        assert len(result["mods"]) == 1
        assert result["mods"][0]["v"] == 99


class TestCheckNoOtherChangesIdBased:
    """check_no_other_changes uses ID-based comparison for lists with _ids."""

    def _ids(self, *items):
        """Build a list of dicts each with an _id and the provided extra fields."""
        return list(items)

    def test_reorder_alone_returns_true(self):
        """A pure reorder of existing items is NOT an external change."""
        b = [{"_id": "x", "v": 1}, {"_id": "y", "v": 2}]
        a = [{"_id": "y", "v": 2}, {"_id": "x", "v": 1}]   # reordered
        c = [{"_id": "x", "v": 1}, {"_id": "y", "v": 2}]   # current = before order
        assert ch.check_no_other_changes({"mods": b}, {"mods": a}, {"mods": c}) is True

    def test_current_matches_after_order_returns_true(self):
        b = [{"_id": "x", "v": 1}, {"_id": "y", "v": 2}]
        a = [{"_id": "y", "v": 2}, {"_id": "x", "v": 1}]
        c = [{"_id": "y", "v": 2}, {"_id": "x", "v": 1}]   # current = after (already applied)
        assert ch.check_no_other_changes({"mods": b}, {"mods": a}, {"mods": c}) is True

    def test_external_content_change_returns_false(self):
        b = [{"_id": "x", "v": 1}]
        a = [{"_id": "x", "v": 5}]   # change intends v 1→5
        c = [{"_id": "x", "v": 99}]  # someone else set v=99
        assert ch.check_no_other_changes({"mods": b}, {"mods": a}, {"mods": c}) is False

    def test_externally_added_item_returns_false(self):
        b = [{"_id": "x", "v": 1}]
        a = [{"_id": "x", "v": 5}]
        c = [{"_id": "x", "v": 1}, {"_id": "z", "v": 7}]  # z added externally
        assert ch.check_no_other_changes({"mods": b}, {"mods": a}, {"mods": c}) is False

    def test_item_removed_externally_when_should_be_updated_returns_false(self):
        """If an item exists in both before and after (being modified) but is
        missing from current, someone removed it externally."""
        b = [{"_id": "x", "v": 1}, {"_id": "y", "v": 2}]
        a = [{"_id": "x", "v": 5}, {"_id": "y", "v": 2}]
        c = [{"_id": "y", "v": 2}]   # x is gone — external removal of modified item
        assert ch.check_no_other_changes({"mods": b}, {"mods": a}, {"mods": c}) is False

    def test_no_ids_falls_back_to_positional(self):
        """Without IDs, the existing positional logic is used."""
        b = {"mods": [{"v": 1}]}
        a = {"mods": [{"v": 5}]}
        c = {"mods": [{"v": 1}]}   # current matches before → OK
        assert ch.check_no_other_changes(b, a, c) is True

    def test_reorder_with_server_calculated_fields_in_current_returns_true(self):
        """Approval must succeed even when the live DB document has server-calculated
        fields that were stripped from the stored before/after items by
        keep_only_differences_list.

        Scenario
        --------
        Two progress_quest items exist.  The user reorders them (A, B → B, A).
        The stored change holds before/after items with only the fields the form
        submitted — no ``total_progress_per_tick`` (a server-calculated field).
        The live DB document includes ``total_progress_per_tick: 5`` on every item.

        check_no_other_changes must return True (no external conflict exists —
        the only difference between stored before and live DB is the extra
        calculated field, which the form never tracked).
        """
        id_a = "aaaaaaaaaaaaaaaaaaaaaaaa"
        id_b = "bbbbbbbbbbbbbbbbbbbbbbbb"

        # Stored before: no calculated fields (stripped by keep_only_differences_list)
        before = {"progress_quests": [
            {"_id": id_a, "quest_name": "Alpha", "slot": 1, "current_progress": 0,
             "required_progress": 10, "bonus_progress_per_tick": 1, "link": ""},
            {"_id": id_b, "quest_name": "Beta",  "slot": 2, "current_progress": 3,
             "required_progress": 10, "bonus_progress_per_tick": 2, "link": ""},
        ]}

        # Stored after: same items reordered (B now first), still no calculated fields
        after = {"progress_quests": [
            {"_id": id_b, "quest_name": "Beta",  "slot": 1, "current_progress": 3,
             "required_progress": 10, "bonus_progress_per_tick": 2, "link": ""},
            {"_id": id_a, "quest_name": "Alpha", "slot": 2, "current_progress": 0,
             "required_progress": 10, "bonus_progress_per_tick": 1, "link": ""},
        ]}

        # Live DB: same content as before-order, but WITH server-calculated field.
        # This extra field must NOT cause a false conflict.
        current = {"progress_quests": [
            {"_id": id_a, "quest_name": "Alpha", "slot": 1, "current_progress": 0,
             "required_progress": 10, "bonus_progress_per_tick": 1, "link": "",
             "total_progress_per_tick": 5},   # server-calculated — not in before/after
            {"_id": id_b, "quest_name": "Beta",  "slot": 2, "current_progress": 3,
             "required_progress": 10, "bonus_progress_per_tick": 2, "link": "",
             "total_progress_per_tick": 5},   # server-calculated — not in before/after
        ]}

        assert ch.check_no_other_changes(before, after, current) is True


class TestIdsAutoAssignedOnRequest:
    """_ensure_item_ids is called inside request_change so new list items get IDs."""

    def test_new_modifier_item_gets_id_on_request(
        self, fake_category_data, mock_mongo, flask_app
    ):
        """After request_change, after_requested_data modifier items have _id."""
        player_id = ObjectId()
        # Insert a real player into the mongomock database so find_one works
        mock_mongo.db.players.insert_one({"_id": player_id, "id": "12345"})

        with (
            patch("helpers.change_helpers.mongo", mock_mongo),
            patch("helpers.change_helpers.category_data", fake_category_data),
            patch("helpers.change_helpers._calculate_and_attach_fields",
                  side_effect=lambda dt, obj: obj),
            patch("helpers.change_helpers.propagate_updates", return_value=None),
        ):
            with flask_app.test_request_context("/"):
                from flask import g
                g.user = {"id": "12345"}

                before = {"modifiers": [{"_id": "existing", "field": "hp", "value": 1}]}
                after  = {"modifiers": [
                    {"_id": "existing", "field": "hp", "value": 1},
                    {"field": "mp", "value": 2},   # new item — no _id yet
                ]}
                change_id = ch.request_change(
                    "nations", ObjectId(), "Update", before, after, "test reason"
                )

        assert change_id is not None
        stored = mock_mongo.db.changes.find_one({"_id": change_id})
        after_mods = stored["after_requested_data"]["modifiers"]
        # New item must have received an _id
        new_item = next(m for m in after_mods if m.get("field") == "mp")
        assert "_id" in new_item
        assert len(new_item["_id"]) == 8
