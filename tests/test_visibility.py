"""
Tests for the nation visibility tier calculation, specifically the
vassal/overlord relationship bonus (+2 tiers, either direction).
"""
from unittest.mock import patch

from bson import ObjectId

from calculations.visibility import compute_visibility, compute_all_visibilities


def _patch_mongo(test_db):
    return patch("calculations.visibility.mongo", **{"db": test_db})


class TestComputeVisibilityVassalOverlord:
    def test_target_is_vassal_of_viewer_gives_plus_two(self, test_db):
        viewer_id = ObjectId()
        target_id = ObjectId()
        viewer_nation = {"_id": viewer_id, "region": None, "overlord": None}
        test_db["nations"].insert_one({
            "_id": target_id, "name": "Vassal Nation", "region": None,
            "overlord": str(viewer_id), "visibility_modifiers": [],
        })
        with patch("calculations.visibility.mongo") as mock_mongo:
            mock_mongo.db = test_db
            tier = compute_visibility(viewer_nation, str(target_id))
        assert tier == 2

    def test_target_is_overlord_of_viewer_gives_plus_two(self, test_db):
        viewer_id = ObjectId()
        target_id = ObjectId()
        viewer_nation = {"_id": viewer_id, "region": None, "overlord": str(target_id)}
        test_db["nations"].insert_one({
            "_id": target_id, "name": "Overlord Nation", "region": None,
            "overlord": None, "visibility_modifiers": [],
        })
        with patch("calculations.visibility.mongo") as mock_mongo:
            mock_mongo.db = test_db
            tier = compute_visibility(viewer_nation, str(target_id))
        assert tier == 2

    def test_no_relation_gives_zero(self, test_db):
        viewer_id = ObjectId()
        target_id = ObjectId()
        viewer_nation = {"_id": viewer_id, "region": None, "overlord": None}
        test_db["nations"].insert_one({
            "_id": target_id, "name": "Unrelated Nation", "region": None,
            "overlord": None, "visibility_modifiers": [],
        })
        with patch("calculations.visibility.mongo") as mock_mongo:
            mock_mongo.db = test_db
            tier = compute_visibility(viewer_nation, str(target_id))
        assert tier == 0

    def test_vassal_bonus_stacks_with_shared_region(self, test_db):
        viewer_id = ObjectId()
        target_id = ObjectId()
        region_id = ObjectId()
        viewer_nation = {"_id": viewer_id, "region": str(region_id), "overlord": None}
        test_db["nations"].insert_one({
            "_id": target_id, "name": "Vassal In Same Region", "region": str(region_id),
            "overlord": str(viewer_id), "visibility_modifiers": [],
        })
        with patch("calculations.visibility.mongo") as mock_mongo:
            mock_mongo.db = test_db
            tier = compute_visibility(viewer_nation, str(target_id))
        assert tier == 3  # +1 region, +2 vassal


class TestComputeAllVisibilitiesVassalOverlord:
    def test_bulk_path_matches_single_path_for_vassal(self, test_db):
        viewer_id = ObjectId()
        target_id = ObjectId()
        viewer_nation = {"_id": viewer_id, "region": None, "overlord": None, "visibility_modifiers": []}
        test_db["nations"].insert_many([
            {"_id": target_id, "name": "Vassal Nation", "region": None,
             "overlord": str(viewer_id), "visibility_modifiers": []},
        ])
        with patch("calculations.visibility.mongo") as mock_mongo:
            mock_mongo.db = test_db
            result = compute_all_visibilities(viewer_nation)
        assert result["Vassal Nation"] == 2

    def test_bulk_path_matches_single_path_for_overlord(self, test_db):
        viewer_id = ObjectId()
        target_id = ObjectId()
        viewer_nation = {"_id": viewer_id, "region": None, "overlord": str(target_id), "visibility_modifiers": []}
        test_db["nations"].insert_many([
            {"_id": target_id, "name": "Overlord Nation", "region": None,
             "overlord": None, "visibility_modifiers": []},
        ])
        with patch("calculations.visibility.mongo") as mock_mongo:
            mock_mongo.db = test_db
            result = compute_all_visibilities(viewer_nation)
        assert result["Overlord Nation"] == 2
