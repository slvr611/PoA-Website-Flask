"""
Regression test for a reported bug: applying a map change (via the map
editor's "Save Directly"/"Submit Change" or approving a pending map change)
visibly succeeds but also surfaces a JSON parse error in the browser.

Root cause: routes/hex_map_routes.py's /api/hex-map/tiles answers a matching
If-None-Match with a bare `Response(status=304)` — no body at all. The
browser's own HTTP cache usually resolves that 304 transparently (serving
the previously cached 200 body back to the calling script), but when it
doesn't, static/js/hex-map.js's loadTiles() called `await resp.json()` on
that empty body unconditionally, with no error handling — unlike every
sibling fetch in the same function, which already guards with
`.catch(() => ({}))`. A failed resp.json() there threw, aborting the rest
of loadTiles() (including the image preloads and the actual render() call)
right after loadTiles() is invoked post-save to refresh the view — so the
underlying tile write had already succeeded, but the UI refresh silently
broke partway through and the browser logged the parse error.

Fixed by having loadTiles() treat a 304 as "nothing changed" and skip
re-parsing/re-applying tiles instead of calling resp.json() on it.

This file pins the SERVER half of that fix's premise: that a matching
If-None-Match really does get back an actual 304 with an empty body (not,
say, a JSON {} body), and that a stale/mismatched ETag still gets the full
tile payload. static/js/hex-map.js's client-side handling isn't executable
here (no JS runtime in this test suite) — see that file's loadTiles() for
the corresponding fix.
"""
import pytest
from bson import ObjectId


@pytest.fixture(autouse=True)
def _ensure_hex_map_routes_registered(flask_app):
    if "hex_map_routes" not in flask_app.blueprints:
        from app_core import mongo, discord
        from routes import register_routes
        register_routes(flask_app, mongo, discord)


@pytest.fixture
def _client(test_db, monkeypatch, flask_app):
    from app_core import mongo as real_mongo
    monkeypatch.setattr(real_mongo, "db", test_db)
    test_db["hex_map_tiles"].insert_one({"q": 0, "r": 0, "terrain": "plains"})
    test_db["global_modifiers"].insert_one({"name": "hex_map_config", "tile_version": 1})
    return flask_app.test_client()


class TestHexMapTilesEtagContract:
    def test_fresh_request_returns_200_with_body_and_an_etag(self, _client):
        resp = _client.get("/api/hex-map/tiles", base_url="https://localhost")
        assert resp.status_code == 200
        assert resp.headers.get("ETag") == '"1"'
        assert resp.get_json()["tiles"]

    def test_matching_if_none_match_returns_a_bare_304_with_no_body(self, _client):
        """The exact condition the JS fix has to handle: a 304 whose body is
        empty, not a JSON object."""
        resp = _client.get(
            "/api/hex-map/tiles", base_url="https://localhost",
            headers={"If-None-Match": '"1"'},
        )
        assert resp.status_code == 304
        assert resp.get_data() == b""

    def test_stale_if_none_match_after_a_tile_change_returns_fresh_200(self, test_db, _client):
        """After a map change bumps tile_version, a client holding the OLD
        ETag must get a full refresh, not another 304 — this is what lets
        the "no changes" 304 path be trusted as actually meaning no changes."""
        from helpers.hex_map_helpers import bump_tile_version
        bump_tile_version()

        resp = _client.get(
            "/api/hex-map/tiles", base_url="https://localhost",
            headers={"If-None-Match": '"1"'},
        )
        assert resp.status_code == 200
        assert resp.headers.get("ETag") == '"2"'
