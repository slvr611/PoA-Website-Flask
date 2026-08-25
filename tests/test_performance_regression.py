"""
Performance regression tests for nation-page load, nation recalculation,
and hex-map tile load.

Every timeout bug found and fixed this session (characters list, the
new-character form, temperament overview) was the same shape: an
unprojected `db.find({})` fetching full documents — including every
calculated/breakdown field — where only a handful of fields were actually
displayed. That pattern is invisible against a handful of dev records and
only shows up as a real production timeout at real data volume, which is
exactly why it kept slipping through. These tests run the same route/
calculation code against a realistic-volume, realistic-shape mongomock
snapshot captured from production (scripts/capture_performance_fixture.py)
so a regression of that shape gets caught here instead of in production.

Thresholds are calibrated against mongomock's speed, not real Atlas network
latency + Heroku's request timeout — mongomock has no network round-trip
cost, so its absolute numbers are much faster than production's. What these
tests actually catch is the RELATIVE regression: fetching far more data (or
making far more round trips) than the page/computation needs. See each
threshold constant's comment for how it was set.

To regenerate the snapshot (e.g. after a schema change makes the old one
stale): python scripts/capture_performance_fixture.py
"""
import os
import time
from copy import deepcopy

import pytest
import mongomock
from bson import json_util

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "performance_snapshot.json")

# Calibrated by running these tests against the real snapshot and observing
# baseline mongomock timing, then leaving generous headroom above that so
# normal CI hardware variance doesn't make this flaky — while still being
# tight enough that reintroducing an unprojected full-collection fetch
# (10-300x more data transferred/deserialized, matching every regression
# actually found this session) clearly trips it.
# Observed baselines: calculate_all_fields ~0.13s, nation page ~0.78s, map ~0.09s.
NATION_RECALCULATION_THRESHOLD_SECONDS = 2.0
NATION_PAGE_LOAD_THRESHOLD_SECONDS = 2.0
MAP_TILE_LOAD_THRESHOLD_SECONDS = 1.5


@pytest.fixture(scope="module", autouse=True)
def ensure_routes_registered(flask_app):
    """Blueprint routes (nation_routes, hex_map_routes, ...) are only
    registered by app.py's top-level register_routes(app, mongo, discord)
    call — conftest.py's flask_app fixture imports app_core.app directly,
    which never runs that, so every route 404s by default in tests. Only
    this module needs real routing (via test_client()), so it's registered
    here rather than in conftest.py, guarded so re-running this module
    (or importing it alongside a future test that also needs routing)
    doesn't hit Flask's "blueprint already registered" error."""
    if "nation_routes" not in flask_app.blueprints:
        from app_core import mongo, discord
        from routes import register_routes
        register_routes(flask_app, mongo, discord)


@pytest.fixture(scope="module")
def perf_db():
    """A mongomock database loaded once with the captured realistic
    snapshot, shared read-only across every test in this module — nothing
    here mutates data in a way that would leak between tests, and reloading
    ~6MB of fixture data per test would make this suite slow for no
    benefit."""
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        snapshot = json_util.loads(f.read())

    client = mongomock.MongoClient()
    db = client["perf_test"]
    for collection_name, docs in snapshot.items():
        if docs:
            db[collection_name].insert_many(docs)
    return db


@pytest.fixture
def patched_mongo(perf_db):
    """Redirects the whole app to perf_db for one test, then restores the
    original state.

    Two things need patching, not one: `app_core.mongo` is a single
    PyMongo instance every module shares via `from app_core import mongo`,
    so mutating its `.db` attribute in place redirects everything that
    calls `mongo.db.X...` directly. But `app_core.category_data[...]
    ["database"]` (used by get_data_on_category/get_data_on_item, which the
    nation page route goes through) captured actual Collection objects at
    import time — reassigning `mongo.db` doesn't retroactively change
    those already-resolved references, so each one is repointed at the
    same-named collection in perf_db individually. The real collection
    name is read off the existing Collection object's `.name` rather than
    the category_data key, since they don't always match (e.g. the "hex_map"
    category's collection is actually named "hex_map_tiles").
    """
    from app_core import mongo as real_mongo, category_data

    original_db = real_mongo.db
    original_category_dbs = {key: info["database"] for key, info in category_data.items()}

    real_mongo.db = perf_db
    for key, info in category_data.items():
        info["database"] = perf_db[original_category_dbs[key].name]

    try:
        yield perf_db
    finally:
        real_mongo.db = original_db
        for key, info in category_data.items():
            info["database"] = original_category_dbs[key]


class TestNationRecalculationPerformance:
    """'Recalculating a nation' = calculate_all_fields, the function
    independently measured elsewhere this session at ~8s/nation against
    real production data — the dominant cost behind both the tick-commit
    timeout and (via the "cache miss" fallback path) nation page loads."""

    def test_calculate_all_fields_stays_under_threshold(self, patched_mongo, flask_app):
        from calculations.field_calculations import calculate_all_fields
        from helpers.data_helpers import get_data_on_category

        schema, db = get_data_on_category("nations")
        nation = db.find_one({})
        assert nation is not None, "fixture has no nations — did capture_performance_fixture.py run?"

        start = time.perf_counter()
        calculate_all_fields(deepcopy(nation), schema, "nation")
        elapsed = time.perf_counter() - start

        assert elapsed < NATION_RECALCULATION_THRESHOLD_SECONDS, (
            f"calculate_all_fields took {elapsed:.2f}s for one nation "
            f"(threshold {NATION_RECALCULATION_THRESHOLD_SECONDS}s) — check for a new "
            f"unprojected query or an accidental O(n) loop over another collection."
        )


class TestNationPageLoadPerformance:
    """'Loading a nation page' = GET /nations/item/<name> (routes/nation_routes.py's
    nation_item), the route that renders nation_owner.html. Requested as an
    anonymous visitor — g.user is None by default with no session cookie set,
    which every ownership/visibility check in the route already handles."""

    def test_nation_item_page_loads_under_threshold(self, patched_mongo, flask_app):
        nation = patched_mongo.nations.find_one({})
        assert nation is not None, "fixture has no nations — did capture_performance_fixture.py run?"

        client = flask_app.test_client()
        start = time.perf_counter()
        response = client.get(f"/nations/item/{nation['name']}", base_url="https://localhost")
        elapsed = time.perf_counter() - start

        assert response.status_code == 200
        assert elapsed < NATION_PAGE_LOAD_THRESHOLD_SECONDS, (
            f"GET /nations/item/{nation['name']} took {elapsed:.2f}s "
            f"(threshold {NATION_PAGE_LOAD_THRESHOLD_SECONDS}s) — check for a new "
            f"unprojected query on this page (see the characters-list/new-character-form/"
            f"temperament-overview fixes earlier this session for the exact pattern)."
        )


class TestMapLoadPerformance:
    """'Loading the map' = GET /api/hex-map/tiles (routes/hex_map_routes.py's
    hex_map_tiles), the endpoint the map page's JS actually fetches tile
    data from on load — the map.html page itself is a near-instant shell."""

    def test_hex_map_tiles_endpoint_loads_under_threshold(self, patched_mongo, flask_app):
        client = flask_app.test_client()
        start = time.perf_counter()
        response = client.get("/api/hex-map/tiles", base_url="https://localhost")
        elapsed = time.perf_counter() - start

        assert response.status_code == 200
        assert elapsed < MAP_TILE_LOAD_THRESHOLD_SECONDS, (
            f"GET /api/hex-map/tiles took {elapsed:.2f}s "
            f"(threshold {MAP_TILE_LOAD_THRESHOLD_SECONDS}s) — check get_all_tiles()'s "
            f"projection in helpers/hex_map_helpers.py hasn't regressed to an unprojected fetch."
        )
