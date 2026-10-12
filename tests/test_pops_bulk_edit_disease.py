"""
Tests for clearing a pop's current disease(s) via the bulk pop edit menu
(routes/pops_routes.py's pops_bulk_edit) — previously only possible one
pop at a time via the admin-only /pops/<id>/cure_disease/<disease_id>
route. Per the user's explicit choice: open to regular nation owners too,
going through the normal request_change/approve_change flow (pending for
non-admins, immediate for admins) — same as every other bulk-edit field
here, not the direct/bypass-approval write cure_pop itself uses.

request_change/approve_change are mocked rather than run for real (would
need category_data["pops"]["database"] repointed, a real players/
global_modifiers setup, etc. — see test_tick_approval_lockout.py's
TestNationJobsSelfApproveRespectsTheLockout for the same tradeoff) so
these tests focus on what THIS route computes and passes to them.
"""
import importlib
from unittest.mock import MagicMock, patch

from bson import ObjectId

pr = importlib.import_module("routes.pops_routes")


def _patch_mongo(test_db):
    fake_mongo = MagicMock()
    fake_mongo.db = test_db
    return patch.object(pr, "mongo", fake_mongo)


def _call_bulk_edit(flask_app, test_db, updates, user):
    with flask_app.test_request_context(
        "/pops/bulk_edit", method="POST", json={"updates": updates},
    ):
        with _patch_mongo(test_db), patch.object(pr, "g") as mock_g, \
             patch.object(pr, "request_change", return_value=ObjectId()) as mock_request, \
             patch.object(pr, "approve_change") as mock_approve:
            mock_g.user = user
            response = pr.pops_bulk_edit()
            return response, mock_request, mock_approve


class TestBulkEditClearDiseases:
    def test_pop_with_diseases_gets_cleared(self, flask_app, test_db):
        pop_id = test_db["pops"].insert_one({
            "nation": "n1", "race": "r1", "culture": "c1", "religion": "g1",
            "diseases": ["d1", "d2"],
        }).inserted_id

        _, mock_request, _ = _call_bulk_edit(
            flask_app, test_db,
            [{"pop_id": str(pop_id), "clear_diseases": True}],
            {"id": "admin-1", "is_admin": True},
        )

        mock_request.assert_called_once()
        after_data = mock_request.call_args.kwargs["after_data"]
        assert after_data["diseases"] == []

    def test_pop_with_pre_disease_race_gets_race_restored(self, flask_app, test_db):
        """Mirrors cure_pop's side effect: curing the LAST disease restores
        whichever race the pop had before a disease changed it."""
        pop_id = test_db["pops"].insert_one({
            "nation": "n1", "race": "sick_race", "culture": "c1", "religion": "g1",
            "diseases": ["d1"], "pre_disease_race": "original_race",
        }).inserted_id

        _, mock_request, _ = _call_bulk_edit(
            flask_app, test_db,
            [{"pop_id": str(pop_id), "clear_diseases": True}],
            {"id": "admin-1", "is_admin": True},
        )

        after_data = mock_request.call_args.kwargs["after_data"]
        assert after_data["diseases"] == []
        assert after_data["race"] == "original_race"
        assert after_data["pre_disease_race"] == ""

    def test_healthy_pop_with_clear_diseases_is_a_no_op(self, flask_app, test_db):
        pop_id = test_db["pops"].insert_one({
            "nation": "n1", "race": "r1", "culture": "c1", "religion": "g1",
            "diseases": [],
        }).inserted_id

        _, mock_request, _ = _call_bulk_edit(
            flask_app, test_db,
            [{"pop_id": str(pop_id), "clear_diseases": True}],
            {"id": "admin-1", "is_admin": True},
        )

        mock_request.assert_not_called()

    def test_clear_diseases_false_leaves_diseases_untouched(self, flask_app, test_db):
        """A normal (non-disease) bulk edit, like a race change, must not
        accidentally touch diseases."""
        pop_id = test_db["pops"].insert_one({
            "nation": "n1", "race": "r1", "culture": "c1", "religion": "g1",
            "diseases": ["d1"],
        }).inserted_id

        _, mock_request, _ = _call_bulk_edit(
            flask_app, test_db,
            [{"pop_id": str(pop_id), "race": "r2"}],
            {"id": "admin-1", "is_admin": True},
        )

        after_data = mock_request.call_args.kwargs["after_data"]
        assert after_data["diseases"] == ["d1"]
        assert after_data["race"] == "r2"

    def test_admin_request_is_approved_immediately(self, flask_app, test_db):
        pop_id = test_db["pops"].insert_one({
            "nation": "n1", "race": "r1", "culture": "c1", "religion": "g1",
            "diseases": ["d1"],
        }).inserted_id

        _, _, mock_approve = _call_bulk_edit(
            flask_app, test_db,
            [{"pop_id": str(pop_id), "clear_diseases": True}],
            {"id": "admin-1", "is_admin": True},
        )

        mock_approve.assert_called_once()

    def test_non_admin_request_stays_pending(self, flask_app, test_db):
        """Per the user's explicit choice: nation owners can request this
        too, but it sits pending for admin approval like any other
        bulk-edit field — it does NOT use cure_pop's direct, bypass-
        approval write."""
        pop_id = test_db["pops"].insert_one({
            "nation": "n1", "race": "r1", "culture": "c1", "religion": "g1",
            "diseases": ["d1"],
        }).inserted_id

        response, mock_request, mock_approve = _call_bulk_edit(
            flask_app, test_db,
            [{"pop_id": str(pop_id), "clear_diseases": True}],
            {"id": "owner-1", "is_admin": False},
        )

        mock_request.assert_called_once()
        mock_approve.assert_not_called()
        assert response.get_json()["approved"] is False
