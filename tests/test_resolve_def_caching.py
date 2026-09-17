"""
Regression tests for _resolve_def's N+1 query bug: a single nation
calculation calls _resolve_def once per district per requirement/job-lookup
site, each of which used to fire its own uncached
district_defs.find_one({"key": def_key}) — measured at 63 separate round
trips for a nation with only 7 districts. Fixed by resolving def_key lookups
through a Flask-request-scoped bulk cache (_get_cached_district_defs_by_key)
that fetches the whole (small, ~49-document) district_defs collection once
per request instead of once per lookup — mirrors the existing
_get_cached_all_tiles/load_db_units g-based caching pattern already used
elsewhere in this file.

Now ALSO falls back to a threading.local cache outside a Flask request
(e.g. the session tick's background thread) — added after live production
evidence (2026-09-17 AI Decision Tick investigation) showed the
no-thread-local-fallback version issuing 68,661 district_defs.find({})
calls for one 218-nation tick, since a tick never runs inside a Flask
request context at all. Safe because every tick spawns a brand new
background Thread (see hex_map_helpers.py's _admin_tile_cache_local and
this file's own _unit_cache_local, the same established pattern) — there is
no previous tick's thread left to collide with. Tests get an autouse
conftest.py fixture (_reset_district_defs_thread_cache) that clears this
cache before every test, since pytest runs all tests in one shared OS
thread with a fresh mongomock db each time — without that reset, one
test's cached district_defs would otherwise leak into the next.
"""
from flask import g

import calculations.field_calculations as fc


class TestGetCachedDistrictDefsByKeyInsideFlaskRequest:
    def test_bulk_fetch_happens_once_per_request(self, flask_app, monkeypatch):
        calls = []
        original_find = None

        class _FakeCursor(list):
            pass

        class _FakeCollection:
            def find(self, *args, **kwargs):
                calls.append((args, kwargs))
                return _FakeCursor([{"key": "courthouse", "modifiers": []}])

        class _FakeDb:
            district_defs = _FakeCollection()

        fake_mongo = type("FakeMongo", (), {"db": _FakeDb()})()
        monkeypatch.setattr(fc, "mongo", fake_mongo)

        with flask_app.test_request_context("/"):
            first = fc._get_cached_district_defs_by_key()
            second = fc._get_cached_district_defs_by_key()

        assert len(calls) == 1, "Expected exactly one district_defs.find() for the whole request"
        assert first is second
        assert first == {"courthouse": {"key": "courthouse", "modifiers": []}}

    def test_resolve_def_uses_the_cache_not_find_one(self, flask_app, monkeypatch):
        find_one_calls = []

        class _FakeCollection:
            def find(self, *args, **kwargs):
                return [{"key": "courthouse", "display_name": "Courthouse"}]

            def find_one(self, *args, **kwargs):
                find_one_calls.append(args)
                return None

        class _FakeDb:
            district_defs = _FakeCollection()

        fake_mongo = type("FakeMongo", (), {"db": _FakeDb()})()
        monkeypatch.setattr(fc, "mongo", fake_mongo)

        with flask_app.test_request_context("/"):
            # Resolve the same def_key many times, as a real multi-district,
            # multi-requirement-check nation calculation would.
            for _ in range(10):
                dd = fc._resolve_def({"def_key": "courthouse"})
                assert dd.get("display_name") == "Courthouse"

        assert find_one_calls == [], "resolve_def must not fall back to find_one when cached"

    def test_cache_is_isolated_per_request(self, flask_app, monkeypatch):
        """A stale cache from one request must never leak into the next —
        each Flask request gets its own flask.g."""
        state = {"defs": [{"key": "a", "v": 1}]}

        class _FakeCollection:
            def find(self, *args, **kwargs):
                return list(state["defs"])

        class _FakeDb:
            district_defs = _FakeCollection()

        fake_mongo = type("FakeMongo", (), {"db": _FakeDb()})()
        monkeypatch.setattr(fc, "mongo", fake_mongo)

        with flask_app.test_request_context("/"):
            first_request_result = fc._get_cached_district_defs_by_key()
        assert first_request_result == {"a": {"key": "a", "v": 1}}

        state["defs"] = [{"key": "a", "v": 2}]
        with flask_app.test_request_context("/"):
            second_request_result = fc._get_cached_district_defs_by_key()
        assert second_request_result == {"a": {"key": "a", "v": 2}}


class TestGetCachedDistrictDefsByKeyOutsideFlaskRequest:
    def test_bulk_fetch_happens_once_per_thread(self, monkeypatch):
        """Outside a Flask request (e.g. this test itself, or the session
        tick's background thread), the thread-local cache means only the
        FIRST call in a given thread queries — later calls in the same
        thread reuse it, exactly like the Flask-g path does per request."""
        calls = []

        class _FakeCollection:
            def find(self, *args, **kwargs):
                calls.append((args, kwargs))
                return [{"key": "courthouse"}]

        class _FakeDb:
            district_defs = _FakeCollection()

        fake_mongo = type("FakeMongo", (), {"db": _FakeDb()})()
        monkeypatch.setattr(fc, "mongo", fake_mongo)

        first = fc._get_cached_district_defs_by_key()
        second = fc._get_cached_district_defs_by_key()

        assert len(calls) == 1, "Expected exactly one district_defs.find() for the whole thread"
        assert first is second
        assert first == {"courthouse": {"key": "courthouse"}}

    def test_a_different_thread_gets_its_own_cache(self, monkeypatch):
        """A separate OS thread (as a separate tick run would be) must never
        see another thread's cached district_defs — each starts fresh."""
        import threading

        state = {"defs": [{"key": "a", "v": 1}]}

        class _FakeCollection:
            def find(self, *args, **kwargs):
                return list(state["defs"])

        class _FakeDb:
            district_defs = _FakeCollection()

        fake_mongo = type("FakeMongo", (), {"db": _FakeDb()})()
        monkeypatch.setattr(fc, "mongo", fake_mongo)

        main_thread_result = fc._get_cached_district_defs_by_key()
        assert main_thread_result == {"a": {"key": "a", "v": 1}}

        state["defs"] = [{"key": "a", "v": 2}]
        other_thread_result = {}
        t = threading.Thread(target=lambda: other_thread_result.update(
            result=fc._get_cached_district_defs_by_key()
        ))
        t.start()
        t.join()

        assert other_thread_result["result"] == {"a": {"key": "a", "v": 2}}
