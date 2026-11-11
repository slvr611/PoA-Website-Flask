"""
Tests for routes/pops_routes.py's pops_flee_pop (POST /pops/flee_pop) --
the admin-only, on-demand equivalent of pop_flee_tick for one specific pop
(see the pop edit page's "Force Flee" button, templates/dataItem.html).

2026-11-09 request: give this manual route the same destination-selection
parity pop_flee_tick already has (helpers/tick_helpers.py) -- a region's
Forced Flee Destination modifier must be respected here too, not just over
a normal tick.
"""
import importlib
import json

import pytest
from unittest.mock import MagicMock, patch
from bson import ObjectId
from flask import g

# routes/__init__.py does `from .pops_routes import pops_routes`, which
# rebinds the `routes` package's `pops_routes` attribute (the submodule
# reference Python auto-registers) to the Blueprint object of the same
# name — so `import routes.pops_routes as x` would bind x to the Blueprint,
# not the module. importlib.import_module reads sys.modules directly by
# full dotted name instead, sidestepping that shadowing. Same issue/fix as
# tests/test_pop_cure_disease_route.py.
pops_routes_module = importlib.import_module("routes.pops_routes")


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


@pytest.fixture(autouse=True)
def _ensure_routes_registered(flask_app):
    if "pops_routes" not in flask_app.blueprints:
        from app_core import mongo, discord
        from routes import register_routes
        register_routes(flask_app, mongo, discord)


def _call_raw(flask_app, test_db, pop_id, is_admin=True):
    """Returns the route's raw return value -- a bare Response for the
    admin_required redirect, or jsonify(...)/jsonify(...), status for
    everything else (not yet run through Flask's own dispatch, so a
    (Response, status) tuple isn't actually a real, status-set Response)."""
    fake_mongo = MagicMock()
    fake_mongo.db = test_db
    with flask_app.test_request_context(
        "/pops/flee_pop", method="POST",
        data=json.dumps({"pop_id": str(pop_id)}), content_type="application/json",
    ):
        g.user = {"id": "admin-1", "is_admin": is_admin}
        with patch.object(pops_routes_module, "mongo", fake_mongo), \
             patch("helpers.tick_helpers.mongo", fake_mongo), \
             patch("helpers.change_helpers.mongo", fake_mongo), \
             patch("helpers.change_helpers.category_data", _fake_category_data(test_db)):
            return pops_routes_module.pops_flee_pop()


def _call(flask_app, test_db, pop_id, is_admin=True):
    """Normalizes _call_raw's result to (json_body, status_code) for the
    success/validation tests, which only ever hit the jsonify(...) paths."""
    result = _call_raw(flask_app, test_db, pop_id, is_admin=is_admin)
    if isinstance(result, tuple):
        response, status = result
    else:
        response, status = result, 200
    return response.get_json(), status


def _setup_pop_and_nation(test_db, region_id, origin_citizenship="Open"):
    test_db["players"].insert_one({"name": "System", "id": "system"})
    origin_id = ObjectId()
    pop_id = ObjectId()
    test_db["nations"].insert_one({
        "_id": origin_id, "name": "Overcrowded Nation", "region": str(region_id),
        "citizenship_stance": origin_citizenship,
    })
    test_db["pops"].insert_one({
        "_id": pop_id, "nation": str(origin_id),
        "race": "Human", "culture": "Culture A", "religion": "Religion A", "job": "farmer",
    })
    return origin_id, pop_id


class TestPopsFleePopRandomDestination:
    def test_moves_pop_to_an_eligible_nation_in_the_same_region_and_approves_change(self, flask_app, test_db):
        region_id = ObjectId()
        origin_id, pop_id = _setup_pop_and_nation(test_db, region_id)
        dest_id = ObjectId()
        test_db["nations"].insert_one({
            "_id": dest_id, "name": "Haven", "region": str(region_id), "citizenship_stance": "Open",
        })
        test_db["regions"].insert_one({"_id": region_id, "modifiers": []})

        body, status = _call(flask_app, test_db, pop_id)

        assert status == 200
        assert body["success"] is True
        assert body["destination"] == "Haven"

        moved_pop = test_db["pops"].find_one({"_id": pop_id})
        assert moved_pop["nation"] == str(dest_id)
        # Untouched fields must survive the round trip.
        assert moved_pop["race"] == "Human"
        assert moved_pop["job"] == "farmer"

        change = test_db["changes"].find_one({})
        assert change["status"] == "Approved"

    def test_closed_nation_in_region_is_never_chosen(self, flask_app, test_db):
        region_id = ObjectId()
        origin_id, pop_id = _setup_pop_and_nation(test_db, region_id)
        closed_id = ObjectId()
        test_db["nations"].insert_one({
            "_id": closed_id, "name": "Fortress", "region": str(region_id), "citizenship_stance": "Closed",
        })
        test_db["regions"].insert_one({"_id": region_id, "modifiers": []})

        body, status = _call(flask_app, test_db, pop_id)

        assert body == {"error": "No eligible destination nations in this region"}
        assert status == 400
        assert test_db["pops"].find_one({"_id": pop_id})["nation"] == str(origin_id)

    def test_no_eligible_candidates_in_region_errors_without_moving_the_pop(self, flask_app, test_db):
        region_id = ObjectId()
        origin_id, pop_id = _setup_pop_and_nation(test_db, region_id)

        body, status = _call(flask_app, test_db, pop_id)

        assert status == 400
        assert test_db["pops"].find_one({"_id": pop_id})["nation"] == str(origin_id)


class TestPopsFleePopForcedDestinationParity:
    """Mirrors helpers/tick_helpers.py's pop_flee_tick: a region's Forced
    Flee Destination modifier is a hard override, bypassing the Closed-
    citizenship filter entirely -- not just a preference in the normal
    pool. 2026-11-09: the manual route previously had no awareness of this
    modifier at all."""

    def test_forced_destination_is_used_even_when_it_is_closed(self, flask_app, test_db):
        region_id = ObjectId()
        origin_id, pop_id = _setup_pop_and_nation(test_db, region_id)
        forced_id = ObjectId()
        test_db["nations"].insert_one({
            "_id": forced_id, "name": "Sanctuary", "region": str(region_id), "citizenship_stance": "Closed",
        })
        other_open_id = ObjectId()
        test_db["nations"].insert_one({
            "_id": other_open_id, "name": "Also Open", "region": str(region_id), "citizenship_stance": "Open",
        })
        test_db["regions"].insert_one({
            "_id": region_id,
            "modifiers": [{"modifier_type": "forced_flee_destination", "target_value": "Sanctuary"}],
        })

        body, status = _call(flask_app, test_db, pop_id)

        assert status == 200
        assert body["success"] is True
        assert body["destination"] == "Sanctuary"
        assert test_db["pops"].find_one({"_id": pop_id})["nation"] == str(forced_id)

    def test_forced_destination_pointing_at_the_pops_own_nation_falls_back_to_random_pool(self, flask_app, test_db):
        region_id = ObjectId()
        origin_id, pop_id = _setup_pop_and_nation(test_db, region_id)
        other_open_id = ObjectId()
        test_db["nations"].insert_one({
            "_id": other_open_id, "name": "Also Open", "region": str(region_id), "citizenship_stance": "Open",
        })
        test_db["regions"].insert_one({
            "_id": region_id,
            "modifiers": [{"modifier_type": "forced_flee_destination", "target_value": "Overcrowded Nation"}],
        })

        body, status = _call(flask_app, test_db, pop_id)

        assert status == 200
        assert body["success"] is True
        assert body["destination"] == "Also Open"
        assert test_db["pops"].find_one({"_id": pop_id})["nation"] == str(other_open_id)


class TestPopsFleePopValidation:
    def test_invalid_pop_id_returns_400(self, flask_app, test_db):
        body, status = _call(flask_app, test_db, "not-an-object-id")
        assert status == 400
        assert body == {"error": "Invalid pop id"}

    def test_missing_pop_returns_404(self, flask_app, test_db):
        body, status = _call(flask_app, test_db, ObjectId())
        assert status == 404

    def test_pop_with_no_nation_returns_400(self, flask_app, test_db):
        pop_id = ObjectId()
        test_db["pops"].insert_one({"_id": pop_id, "nation": ""})
        body, status = _call(flask_app, test_db, pop_id)
        assert status == 400
        assert body == {"error": "Pop has no nation"}

    def test_nation_with_no_region_returns_400(self, flask_app, test_db):
        origin_id = ObjectId()
        pop_id = ObjectId()
        test_db["nations"].insert_one({"_id": origin_id, "name": "Landless"})
        test_db["pops"].insert_one({"_id": pop_id, "nation": str(origin_id)})
        body, status = _call(flask_app, test_db, pop_id)
        assert status == 400
        assert body == {"error": "Nation has no region"}


class TestPopsFleePopAdminGate:
    def test_non_admin_cannot_trigger_flee(self, flask_app, test_db):
        region_id = ObjectId()
        origin_id, pop_id = _setup_pop_and_nation(test_db, region_id)
        dest_id = ObjectId()
        test_db["nations"].insert_one({
            "_id": dest_id, "name": "Haven", "region": str(region_id), "citizenship_stance": "Open",
        })
        test_db["regions"].insert_one({"_id": region_id, "modifiers": []})

        response = _call_raw(flask_app, test_db, pop_id, is_admin=False)

        assert response.status_code in (301, 302), "a non-admin must be redirected away, not allowed to flee the pop"
        assert test_db["pops"].find_one({"_id": pop_id})["nation"] == str(origin_id)
