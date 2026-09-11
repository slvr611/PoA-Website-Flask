"""
Regression test for a real gap: there was no way to remove a disease from a
pop via that pop's edit page — json-data/schemas/pops.json's `diseases`
field is an array of linked_object, a shape forms.py's DynamicSchemaForm
never implements a form field for (see its array/linked_object branch),
so it silently never appeared in the generic edit form at all.

Fixed with a small admin-only "Diseases" section on the pop's edit page
(templates/dataItem.html, gated on data_type == "pops") listing each
currently-assigned disease with a "Remove" button, backed by
routes.pops_routes.pops_cure_disease — mirrors diseases_item.html's
existing "Cure Nation's Pops" tool (same direct cure_pop call, not routed
through the change-request workflow), just scoped to one pop.
"""
import importlib
import pytest
from unittest.mock import patch
from bson import ObjectId
from flask import g

# routes/__init__.py does `from .pops_routes import pops_routes`, which
# overwrites the `routes` package's `pops_routes` attribute (the submodule
# reference Python auto-registers) with the Blueprint object of the same
# name — so `import routes.pops_routes as x` would bind x to the Blueprint,
# not the module. importlib.import_module reads sys.modules directly by
# full dotted name instead, sidestepping that shadowing.
pops_routes_module = importlib.import_module("routes.pops_routes")


@pytest.fixture(autouse=True)
def _ensure_routes_registered(flask_app):
    if "pops_routes" not in flask_app.blueprints:
        from app_core import mongo, discord
        from routes import register_routes
        register_routes(flask_app, mongo, discord)


def _call_as_admin(flask_app, test_db, pop_id, disease_id):
    with flask_app.test_request_context(f"/pops/{pop_id}/cure_disease/{disease_id}", method="POST"):
        g.user = {"id": "admin-1", "is_admin": True}
        with patch.object(pops_routes_module, "mongo", type("M", (), {"db": test_db})()), \
             patch("helpers.disease_helpers.mongo", type("M", (), {"db": test_db})()):
            return pops_routes_module.pops_cure_disease(str(pop_id), str(disease_id))


class TestPopsCureDiseaseRoute:
    def test_removes_disease_from_pop_and_redirects_to_edit_page(self, flask_app, test_db):
        disease_id = ObjectId()
        test_db["diseases"].insert_one({"_id": disease_id, "name": "Mire Madness"})
        pop_id = ObjectId()
        test_db["pops"].insert_one({
            "_id": pop_id, "nation": "n1", "diseases": [str(disease_id)],
        })

        response = _call_as_admin(flask_app, test_db, pop_id, disease_id)

        assert response.status_code in (301, 302)
        assert response.location.endswith(f"/pops/edit/{pop_id}")
        updated = test_db["pops"].find_one({"_id": pop_id})
        assert updated["diseases"] == []

    def test_pop_with_two_diseases_only_removes_the_targeted_one(self, flask_app, test_db):
        disease_a = ObjectId()
        disease_b = ObjectId()
        test_db["diseases"].insert_one({"_id": disease_a, "name": "Disease A"})
        test_db["diseases"].insert_one({"_id": disease_b, "name": "Disease B"})
        pop_id = ObjectId()
        test_db["pops"].insert_one({
            "_id": pop_id, "nation": "n1", "diseases": [str(disease_a), str(disease_b)],
        })

        _call_as_admin(flask_app, test_db, pop_id, disease_a)

        updated = test_db["pops"].find_one({"_id": pop_id})
        assert updated["diseases"] == [str(disease_b)]

    def test_missing_pop_flashes_error_and_redirects_to_pops_list(self, flask_app, test_db):
        response = _call_as_admin(flask_app, test_db, ObjectId(), ObjectId())

        assert response.status_code in (301, 302)
        assert response.location.endswith("/pops")

    def test_non_admin_is_redirected_away_without_curing(self, flask_app, test_db):
        disease_id = ObjectId()
        test_db["diseases"].insert_one({"_id": disease_id, "name": "Mire Madness"})
        pop_id = ObjectId()
        test_db["pops"].insert_one({
            "_id": pop_id, "nation": "n1", "diseases": [str(disease_id)],
        })

        with flask_app.test_request_context(f"/pops/{pop_id}/cure_disease/{disease_id}", method="POST"):
            g.user = {"id": "player-1", "is_admin": False}
            with patch.object(pops_routes_module, "mongo", type("M", (), {"db": test_db})()):
                pops_routes_module.pops_cure_disease(str(pop_id), str(disease_id))

        updated = test_db["pops"].find_one({"_id": pop_id})
        assert updated["diseases"] == [str(disease_id)], "a non-admin must not be able to cure a pop's disease"
