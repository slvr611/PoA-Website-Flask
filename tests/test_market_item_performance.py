"""
Regression test for the /markets/item/<name> N+1 query bug: for every
market member, the route issued a separate, UNPROJECTED nations.find_one
just to read resource_desires off each nation — on a real (non-local) DB
connection this meant one full ~60KB document fetch per member. A 30-member
market (e.g. "Avernal Market") took ~19s just for this loop.

Root cause (routes/data_item_routes.py, market_item):
  1. It re-queried market_links even though get_linked_objects already
     resolves "members" via the exact same join table for display.
  2. It then looped member ids and called nations_db.find_one(...) with NO
     projection, once per member.

Fixed by reusing get_linked_objects's already-resolved member ids and
replacing the loop with a single batched, projected
nations_db.find({"_id": {"$in": [...]}}, {"name": 1, "resource_desires": 1})
call — one round trip instead of N, and no unneeded full-document payload.
"""
import importlib
import mongomock
from bson import ObjectId
from flask import g

from app_core import category_data
import helpers.render_helpers as render_helpers_module

# routes/__init__.py does `from .data_item_routes import data_item_routes`
# (the Blueprint), which overwrites the `routes.data_item_routes` package
# attribute with the Blueprint object — `import routes.data_item_routes as x`
# would bind x to the Blueprint, not the module. importlib.import_module
# reads sys.modules directly by dotted name, side-stepping that. Same
# technique as test_pop_cure_disease_route.py / test_district_upgrade_visibility.py.
data_item_routes_module = importlib.import_module("routes.data_item_routes")
market_item = data_item_routes_module.market_item


def _setup_market_with_members(monkeypatch, member_count):
    client = mongomock.MongoClient()
    db = client["test"]

    market_id = ObjectId()
    db["markets"].insert_one({"_id": market_id, "name": "Avernal Market"})

    member_ids = []
    for i in range(member_count):
        nation_id = ObjectId()
        member_ids.append(nation_id)
        db["nations"].insert_one({
            "_id": nation_id,
            "name": f"Nation {i}",
            "resource_desires": [
                {"resource": "gold", "trade_type": "Import", "price": 10, "quantity": 5},
            ],
            # A large, irrelevant field to prove the fix doesn't fetch it —
            # the old unprojected find_one would pull this every time.
            "job_details": {"padding": "x" * 10_000},
        })
        db["market_links"].insert_one({
            "market": str(market_id), "member": str(nation_id),
            "market_safety_stance": "Ignore",
        })

    monkeypatch.setitem(category_data["markets"], "database", db["markets"])
    monkeypatch.setitem(category_data["nations"], "database", db["nations"])
    monkeypatch.setitem(category_data["market_links"], "database", db["market_links"])
    fake_mongo = type("FakeMongo", (), {"db": db})()
    monkeypatch.setattr(render_helpers_module, "mongo", fake_mongo)

    return market_id, member_ids, db


class TestMarketItemDoesNotFetchFullNationDocumentsPerMember:
    def test_no_unprojected_find_one_on_nations(self, monkeypatch, flask_app):
        """get_linked_objects's own per-member find_one loop (for the
        "members" display list) is pre-existing and out of scope here — it
        already uses a small projection and isn't the bug (confirmed: ~45ms/
        query vs. ~630ms/query for the old unprojected resource_desires
        loop this test targets). What must never happen again is a nations
        find_one with NO projection (a full ~60KB document fetch) — that
        was the old code's actual N+1 cost, now replaced by a single
        batched find() (see test_projection_excludes_unneeded_fields)."""
        market_id, member_ids, db = _setup_market_with_members(monkeypatch, member_count=5)

        original_find_one = db["nations"].find_one
        unprojected_calls = []

        def _tracking_find_one(filter=None, projection=None, *args, **kwargs):
            if projection is None:
                unprojected_calls.append(filter)
            return original_find_one(filter, projection, *args, **kwargs)

        monkeypatch.setattr(db["nations"], "find_one", _tracking_find_one)

        with flask_app.test_request_context(f"/markets/item/{market_id}"):
            g.user = None
            market_item(str(market_id))

        assert unprojected_calls == [], (
            f"Expected zero unprojected (full-document) nations.find_one calls, "
            f"got {len(unprojected_calls)}: {unprojected_calls}"
        )

    def test_resource_desires_still_correctly_populated(self, monkeypatch, flask_app):
        market_id, member_ids, db = _setup_market_with_members(monkeypatch, member_count=3)

        with flask_app.test_request_context(f"/markets/item/{market_id}"):
            g.user = None
            html = market_item(str(market_id))
            html = html if isinstance(html, str) else html.get_data(as_text=True)

        # All 3 members' resource desires should appear.
        assert html.count("gold") >= 3

    def test_projection_excludes_unneeded_fields(self, monkeypatch, flask_app):
        """Confirms the batched query only asks for name/resource_desires —
        not the whole document (the actual fix for the ~60KB/doc cost).

        pymongo's find_one is implemented in terms of find() internally
        (mongomock mirrors this), so get_linked_objects_parallel's own
        per-member "members" display lookups also show up here as find()
        calls with their own small ({_id, market_safety_stance, name})
        projection — a separate, pre-existing, out-of-scope code path.
        Filter to the resource_desires-shaped call specifically, which only
        the fixed batched query produces."""
        market_id, member_ids, db = _setup_market_with_members(monkeypatch, member_count=2)

        original_find = db["nations"].find
        seen_projections = []

        def _tracking_find(filter=None, projection=None, *args, **kwargs):
            # Snapshot a copy — mongomock's find() may normalize/mutate the
            # projection dict object in place as part of query execution,
            # which would otherwise corrupt this recorded value after the
            # fact since list.append stores a reference, not a copy.
            seen_projections.append(dict(projection) if projection else projection)
            return original_find(filter, projection, *args, **kwargs)

        monkeypatch.setattr(db["nations"], "find", _tracking_find)

        with flask_app.test_request_context(f"/markets/item/{market_id}"):
            g.user = None
            market_item(str(market_id))

        resource_desire_calls = [p for p in seen_projections if p and "resource_desires" in p]
        assert len(resource_desire_calls) == 1, "Expected exactly one batched resource_desires find() call"
        assert resource_desire_calls[0] == {"name": 1, "resource_desires": 1}


class TestMarketItemUsesParallelLinkedObjects:
    def test_market_item_uses_parallel_fetch(self, monkeypatch, flask_app):
        """Guards the secondary fix — market_item should call
        get_linked_objects_parallel, not the sequential get_linked_objects,
        to avoid summing independent round trips."""
        market_id, member_ids, db = _setup_market_with_members(monkeypatch, member_count=1)

        called_with = []
        original = data_item_routes_module.get_linked_objects_parallel

        def _tracking(*args, **kwargs):
            called_with.append(args)
            return original(*args, **kwargs)

        monkeypatch.setattr(data_item_routes_module, "get_linked_objects_parallel", _tracking)

        with flask_app.test_request_context(f"/markets/item/{market_id}"):
            g.user = None
            market_item(str(market_id))

        assert len(called_with) == 1
