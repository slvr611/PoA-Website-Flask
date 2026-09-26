"""
Regression test for a nation page load taking 30+ seconds — diagnosed live
against "Archonate of Vyssafia" (2 pending Update change requests): the
"pending change preview" block in routes/nation_routes.py's nation_item ran
a full, completely uncached calculate_all_fields on EVERY page view whenever
ANY pending "Update" change existed for that nation — unlike the nation's
own state, whose breakdowns/calculated fields ARE cached on the document
and only recomputed when actually stale.

Measured live: 24-37s for a nation with 2 pending changes on the first
load; identical load pattern on the fixture's realistic-volume data used
here to confirm the caching fix (not on absolute wall-clock — mongomock has
no network latency, see test_performance_regression.py's own calibration
note — but on call count: calculate_all_fields must not be invoked again
for the SAME pending changes on a repeat view).

Fixed by caching the preview (pending_nation/pending_breakdowns) on the
nation document under _pending_preview_cache, keyed by a signature of the
nation's own last-calculated state (a hash of its breakdowns) plus exactly
which pending changes exist and each one's last_modified_time — so editing,
adding, or withdrawing a pending change (or a real recalculation of the
nation itself) still correctly invalidates it, but a repeat view of an
unchanged pending state does not recompute.
"""
import importlib
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from bson import ObjectId
from flask import g

# routes/__init__.py does `from .nation_routes import nation_routes` (the
# Blueprint), which overwrites the `routes.nation_routes` package attribute
# with the Blueprint object — `import routes.nation_routes as nr` would
# bind nr to the Blueprint, not the module. importlib.import_module reads
# sys.modules directly by dotted name, side-stepping that. Same technique
# as test_pop_cure_disease_route.py / test_market_item_performance.py.
nr = importlib.import_module("routes.nation_routes")


def _pending_change(nation_id, after_data, modified_time=None):
    return {
        "_id": ObjectId(),
        "target": nation_id,
        "target_collection": "nations",
        "change_type": "Update",
        "status": "Pending",
        "time_requested": modified_time or datetime.now(timezone.utc),
        "last_modified_time": modified_time or datetime.now(timezone.utc),
        "after_requested_data": after_data,
    }


@pytest.fixture
def vyssafia_like_nation(test_db, monkeypatch):
    """A minimal but fully-calculated nation (real schema, real
    calculate_all_fields pass) with cached breakdowns already present —
    matching the "nation's own state is cached, only the pending-change
    preview wasn't" shape of the real bug.

    get_data_on_item("nations", ...) reads via
    category_data["nations"]["database"], a Collection object captured at
    import time — patching mongo.db alone doesn't retroactively redirect
    it, so it's repointed at test_db directly here too (same pattern as
    test_performance_regression.py's patched_mongo fixture)."""
    from app_core import category_data
    from calculations.field_calculations import calculate_all_fields

    monkeypatch.setitem(category_data["nations"], "database", test_db["nations"])
    schema = category_data["nations"]["schema"]
    nation_id = ObjectId()
    base = {"_id": nation_id, "name": "Testland", "money": 100, "modifiers": []}
    calculated, breakdowns = calculate_all_fields(dict(base), schema, "nation", return_breakdowns=True)
    base.update(calculated)
    base["breakdowns"] = breakdowns
    test_db["nations"].insert_one(base)
    return test_db["nations"].find_one({"_id": nation_id})


class TestPendingPreviewCache:
    def test_second_view_does_not_recompute_for_the_same_pending_changes(
        self, vyssafia_like_nation, test_db, flask_app, monkeypatch
    ):
        nation = vyssafia_like_nation
        test_db["changes"].insert_one(_pending_change(nation["_id"], {"money": 500}))

        calls = []
        real_calculate_all_fields = nr.calculate_all_fields

        def _counting_calculate_all_fields(*args, **kwargs):
            calls.append(1)
            return real_calculate_all_fields(*args, **kwargs)

        monkeypatch.setattr(nr, "calculate_all_fields", _counting_calculate_all_fields)
        monkeypatch.setattr(nr, "mongo", type("M", (), {"db": test_db})())

        with flask_app.test_request_context("/nations/item/Testland"):
            g.user = None
            nr.nation_item("Testland")
        first_call_count = len(calls)
        assert first_call_count >= 1, "expected the pending-preview branch to compute at least once"

        # Re-fetch — the route persists _pending_preview_cache back to Mongo.
        with flask_app.test_request_context("/nations/item/Testland"):
            g.user = None
            nr.nation_item("Testland")

        assert len(calls) == first_call_count, (
            "calculate_all_fields was called again on a repeat view with the "
            "exact same pending changes — the preview cache did not take effect"
        )

    def test_editing_the_pending_change_invalidates_the_cache(
        self, vyssafia_like_nation, test_db, flask_app, monkeypatch
    ):
        nation = vyssafia_like_nation
        change = _pending_change(nation["_id"], {"money": 500})
        test_db["changes"].insert_one(change)

        calls = []
        real_calculate_all_fields = nr.calculate_all_fields

        def _counting_calculate_all_fields(*args, **kwargs):
            calls.append(1)
            return real_calculate_all_fields(*args, **kwargs)

        monkeypatch.setattr(nr, "calculate_all_fields", _counting_calculate_all_fields)
        monkeypatch.setattr(nr, "mongo", type("M", (), {"db": test_db})())

        with flask_app.test_request_context("/nations/item/Testland"):
            g.user = None
            nr.nation_item("Testland")
        first_call_count = len(calls)

        # The pending change is edited (a later last_modified_time, and
        # different requested data) — a real edit, not a repeat view.
        test_db["changes"].update_one(
            {"_id": change["_id"]},
            {"$set": {
                "after_requested_data": {"money": 999},
                "last_modified_time": datetime.now(timezone.utc),
            }},
        )

        with flask_app.test_request_context("/nations/item/Testland"):
            g.user = None
            nr.nation_item("Testland")

        assert len(calls) > first_call_count, (
            "editing the pending change should invalidate the cached preview "
            "and trigger a fresh calculate_all_fields call"
        )

    def test_no_pending_changes_skips_the_preview_entirely(
        self, vyssafia_like_nation, test_db, flask_app, monkeypatch
    ):
        calls = []
        real_calculate_all_fields = nr.calculate_all_fields

        def _counting_calculate_all_fields(*args, **kwargs):
            calls.append(1)
            return real_calculate_all_fields(*args, **kwargs)

        monkeypatch.setattr(nr, "calculate_all_fields", _counting_calculate_all_fields)
        monkeypatch.setattr(nr, "mongo", type("M", (), {"db": test_db})())

        with flask_app.test_request_context("/nations/item/Testland"):
            g.user = None
            nr.nation_item("Testland")

        assert calls == [], "no pending changes exist — calculate_all_fields should never run"
