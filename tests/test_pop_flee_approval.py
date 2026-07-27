"""
Regression test for a bug where pops fleeing overcrowding never actually
moved: pop_flee_tick's mongo projection for the fleeing pop omitted the
"nation" field (it was only used as the query filter, not projected back),
so before_data had no "nation" key at all. system_approve_change's
check_no_other_changes compares before_data against the pop's live document
field-by-field, and a field entirely absent from before_data can never equal
the pop's real (non-None) current nation — so the check always failed and
the change was silently stranded at "Pending" forever, with no error or log
anywhere (system_approve_change, unlike the human-facing approve_change,
had no diagnostic output on this path either).

This test exercises the real system_request_change / system_approve_change
pipeline (not mocked, unlike tests/test_forced_flee_destination.py) so it
would have caught the bug: it asserts the resulting change is actually
"Approved" and the pop's nation field is actually updated, not just that
pop_flee_tick's own destination-selection logic ran correctly.
"""
from unittest.mock import patch, MagicMock
from bson import ObjectId

import mongomock

import helpers.tick_helpers as th


def _fake_category_data(test_db):
    empty_schema = {"properties": {}, "external_calculation_requirements": {}}
    return {
        "pops": {
            "pluralName": "Pops", "singularName": "Pop",
            "database": test_db["pops"], "schema": empty_schema,
        },
        "changes": {
            "pluralName": "Changes", "singularName": "Change",
            "database": test_db["changes"], "schema": empty_schema,
        },
    }


class TestPopFleeTickActuallyGetsApproved:
    def test_fled_pop_change_is_approved_and_nation_updated(self):
        client = mongomock.MongoClient()
        test_db = client["poa_test"]

        origin_id = ObjectId()
        dest_id = ObjectId()
        pop_id = ObjectId()
        region_id = ObjectId()

        test_db["players"].insert_one({"name": "System", "id": "system"})
        test_db["pops"].insert_one({
            "_id": pop_id, "nation": str(origin_id),
            "race": "Human", "culture": "Culture A", "religion": "Religion A",
            "job": "farmer",  # an extra field not touched by the move
        })

        old_nation = {
            "_id": origin_id, "name": "Overcrowded Nation", "region": str(region_id),
            "pop_count": 20, "effective_pop_capacity": 10, "pop_flee_chance": 1.0,
        }
        new_nation = dict(old_nation)
        dest_nation = {"_id": dest_id, "name": "Haven"}

        fake_mongo = MagicMock()
        fake_mongo.db = test_db
        test_db["regions"].insert_one({"_id": region_id, "modifiers": []})
        test_db["nations"].insert_one(dest_nation | {"region": str(region_id), "citizenship_stance": "Open"})

        with patch("helpers.tick_helpers.mongo", fake_mongo), \
             patch("helpers.change_helpers.mongo", fake_mongo), \
             patch("helpers.change_helpers.category_data", _fake_category_data(test_db)):
            result = th.pop_flee_tick(old_nation, new_nation, {})

        assert "fled" in result.lower()

        change = test_db["changes"].find_one({})
        assert change is not None, "pop_flee_tick did not create a change request"
        assert change["status"] == "Approved", (
            f"change was left as {change['status']!r} instead of Approved — "
            "this is the bug: the pop never actually moves"
        )

        moved_pop = test_db["pops"].find_one({"_id": pop_id})
        assert moved_pop["nation"] == str(dest_id)
        # Untouched fields must survive the round trip.
        assert moved_pop["race"] == "Human"
        assert moved_pop["job"] == "farmer"
