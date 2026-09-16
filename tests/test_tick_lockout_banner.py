"""
Tests for the tick-lockout banner (g.tick_in_progress), the automatic
counterpart to the existing manually-toggled revert-warning banner. Reads
the same tick_status.running heartbeat helpers/change_helpers.py's
is_tick_locked() uses to gate approval — but through its own short-TTL
process cache (routes/base_routes.py's _get_tick_in_progress), since this
is just a display banner, not the actual enforcement point.
"""
import importlib
from unittest.mock import MagicMock, patch

import mongomock

# routes/__init__.py does `from .base_routes import base_routes`, which
# rebinds the `base_routes` attribute on the `routes` package to the
# Blueprint object — so `import routes.base_routes as br` (attribute lookup
# through the package) resolves to the Blueprint, not the module. Pulling
# straight from sys.modules avoids that shadowing (same technique as
# test_change_approval_flash.py).
br = importlib.import_module("routes.base_routes")


def _fake_mongo(test_db):
    m = MagicMock()
    m.db = test_db
    return m


class TestGetTickInProgress:
    def setup_method(self):
        # Each test gets a clean cache — the module-level dict persists
        # across tests otherwise.
        br._tick_lock_cache.update({"active": False, "ts": 0.0})

    def test_false_when_no_tick_status_document_exists(self):
        test_db = mongomock.MongoClient()["poa_test"]
        with patch.object(br, "mongo", _fake_mongo(test_db)):
            assert br._get_tick_in_progress() is False

    def test_true_while_tick_status_running_is_true(self):
        test_db = mongomock.MongoClient()["poa_test"]
        test_db["tick_status"].insert_one({"_id": "current", "running": True})
        with patch.object(br, "mongo", _fake_mongo(test_db)):
            assert br._get_tick_in_progress() is True

    def test_result_is_cached_within_the_ttl(self):
        test_db = mongomock.MongoClient()["poa_test"]
        test_db["tick_status"].insert_one({"_id": "current", "running": True})
        with patch.object(br, "mongo", _fake_mongo(test_db)) as fake_mongo:
            assert br._get_tick_in_progress() is True
            # Flip the underlying data without advancing time — the cached
            # value must still be served, not a fresh read.
            test_db["tick_status"].update_one({"_id": "current"}, {"$set": {"running": False}})
            assert br._get_tick_in_progress() is True

    def test_cache_expires_after_the_ttl(self):
        test_db = mongomock.MongoClient()["poa_test"]
        test_db["tick_status"].insert_one({"_id": "current", "running": True})
        with patch.object(br, "mongo", _fake_mongo(test_db)), \
             patch.object(br, "_time_mod") as mock_time:
            mock_time.time.return_value = 1000.0
            assert br._get_tick_in_progress() is True

            test_db["tick_status"].update_one({"_id": "current"}, {"$set": {"running": False}})
            mock_time.time.return_value = 1000.0 + br._TICK_LOCK_CACHE_TTL + 1
            assert br._get_tick_in_progress() is False


class TestInjectTickInProgress:
    def test_sets_g_tick_in_progress_from_the_cache_getter(self, flask_app):
        with flask_app.test_request_context():
            with patch.object(br, "_get_tick_in_progress", return_value=True):
                br.inject_tick_in_progress()
                from flask import g
                assert g.tick_in_progress is True
