"""
Regression coverage for sync_nation_cities's duplicate-placement guard —
the city counterpart of tests/test_sync_nation_districts.py's
TestSyncNationDistrictsDuplicateGuard.

Live data showed the same bug on the city side: Twinborn Elitria's
"vandadorian_citadel" city existed on two map tiles 7 hexes apart under the
same id, because sync_nation_cities's "Nation -> Map" direction only checked
whether the id was on one of THIS nation's own currently-owned tiles before
placing a new copy — never whether that id already existed on some tile it
just didn't see as owned right now. This test locks in the fix: such an id
is now skipped and reported instead of duplicated.
"""
from unittest.mock import patch
from bson import ObjectId

import helpers.ai_decision_helpers as adh


def _patched_mongo(test_db):
    fake_mongo = type("FakeMongo", (), {"db": test_db})()
    return patch.object(adh, "mongo", fake_mongo)


class TestSyncNationCitiesDuplicateGuard:
    def test_city_id_already_on_the_map_elsewhere_is_not_duplicated(self, test_db):
        nation = {
            "_id": ObjectId(), "name": "Test Nation", "government_type": "Standard",
            "cities": [{"_id": "existing1", "name": "", "type": "generic", "node": "", "wall": ""}],
        }
        # This city's real tile — owned by someone else (or otherwise
        # excluded from this nation's own tiles_with_city/owned_tiles),
        # exactly the situation that let the original bug create a ghost.
        test_db["hex_map_tiles"].insert_one({
            "q": 5, "r": 5, "owner": "Some Other Nation",
            "city": {"id": "existing1", "name": "", "type": "generic"},
        })
        with _patched_mongo(test_db):
            report = adh.sync_nation_cities(nation, dry_run=False, tiles_with_city=[], owned_tiles=[])

        assert report["placed_on_map"] == []
        assert report["unplaceable"] == []
        assert report["skipped_duplicate_elsewhere"] == [
            {"id": "existing1", "name": "", "type": "generic"},
        ]
        # No second tile was created for this id.
        assert test_db["hex_map_tiles"].count_documents({"city.id": "existing1"}) == 1

    def test_world_city_ids_param_is_honored_without_a_query(self, test_db):
        nation = {
            "_id": ObjectId(), "name": "Test Nation", "government_type": "Standard",
            "cities": [{"_id": "existing1", "name": "", "type": "generic", "node": "", "wall": ""}],
        }
        with _patched_mongo(test_db):
            report = adh.sync_nation_cities(
                nation, dry_run=False, tiles_with_city=[], owned_tiles=[],
                world_city_coords=set(), world_city_ids={"existing1"},
            )

        assert report["placed_on_map"] == []
        assert report["skipped_duplicate_elsewhere"] == [
            {"id": "existing1", "name": "", "type": "generic"},
        ]
