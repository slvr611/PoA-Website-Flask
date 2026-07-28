"""
Regression test for a production crash: a tick thread died with
`KeyError: '_id'` deep in tick_helpers.py's nation-save loop, on a nation
that had been processed successfully by every earlier tick function.

Root cause: system_request_change/request_change do `before_data.pop("_id",
None)` (to keep it out of the stored diff) directly on whatever dict they're
handed — mutating it in place rather than a copy. Every call site in the
codebase deepcopies before_data first... except generate_ai_character
(tick_helpers.py), which passed `org` itself (the exact same dict object as
tick()'s old_nations[i]) straight through as before_data when an AI ruler's
succession updates the nation's primary demographics. That silently stripped
"_id" from old_nations[i], which only surfaced ~2900 lines later when the
save loop's strict `old_nations[i]["_id"]` finally ran for that nation.

This test exercises the real (unmocked) request_change/system_request_change
functions directly and asserts the CALLER'S dict is left untouched — it
would have caught the bug regardless of which call site introduced it.
"""
from unittest.mock import patch, MagicMock
from copy import deepcopy

import mongomock

import helpers.change_helpers as ch


def _fake_category_data(test_db):
    empty_schema = {"properties": {}, "external_calculation_requirements": {}}
    return {
        "nations": {
            "pluralName": "Nations", "singularName": "Nation",
            "database": test_db["nations"], "schema": empty_schema,
        },
        "changes": {
            "pluralName": "Changes", "singularName": "Change",
            "database": test_db["changes"], "schema": empty_schema,
        },
    }


class TestSystemRequestChangeDoesNotMutateCallerDicts:
    def test_before_data_keeps_its_id_after_the_call(self):
        client = mongomock.MongoClient()
        test_db = client["poa_test"]
        test_db["players"].insert_one({"name": "System", "id": "system"})

        nation_id = mongomock.ObjectId()
        # This is the caller's live object — e.g. tick()'s old_nations[i] —
        # not a throwaway. The bug was that passing it as before_data would
        # strip "_id" from THIS SAME dict as a side effect.
        live_nation_dict = {"_id": nation_id, "name": "Testland", "money": 100}
        after = deepcopy(live_nation_dict)
        after["money"] = 200

        fake_mongo = MagicMock()
        fake_mongo.db = test_db
        with patch.object(ch, "mongo", fake_mongo), \
             patch.object(ch, "category_data", _fake_category_data(test_db)):
            ch.system_request_change(
                data_type="nations",
                item_id=nation_id,
                change_type="Update",
                before_data=live_nation_dict,
                after_data=after,
                reason="test",
            )

        assert "_id" in live_nation_dict, (
            "system_request_change mutated the caller's before_data dict, "
            "stripping _id — this is the tick KeyError bug"
        )
        assert live_nation_dict["_id"] == nation_id
        assert live_nation_dict["money"] == 100  # untouched by the "after" side too


class TestRequestChangeDoesNotMutateCallerDicts:
    def test_before_data_keeps_its_id_after_the_call(self, flask_app):
        client = mongomock.MongoClient()
        test_db = client["poa_test"]
        player_id = mongomock.ObjectId()
        test_db["players"].insert_one({"_id": player_id, "name": "Someone", "id": "discorduser"})

        nation_id = mongomock.ObjectId()
        live_nation_dict = {"_id": nation_id, "name": "Testland", "money": 100}
        after = deepcopy(live_nation_dict)
        after["money"] = 200

        fake_mongo = MagicMock()
        fake_mongo.db = test_db
        with flask_app.test_request_context():
            with patch.object(ch, "mongo", fake_mongo), \
                 patch.object(ch, "category_data", _fake_category_data(test_db)), \
                 patch.object(ch, "g") as mock_g:
                mock_g.user = {"id": "discorduser"}
                ch.request_change(
                    data_type="nations",
                    item_id=nation_id,
                    change_type="Update",
                    before_data=live_nation_dict,
                    after_data=after,
                    reason="test",
                )

        assert "_id" in live_nation_dict
        assert live_nation_dict["_id"] == nation_id
