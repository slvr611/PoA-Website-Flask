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

No thread-local fallback (unlike those two) — see
_get_cached_district_defs_by_key's docstring for why: this codebase's tests
call _resolve_def-consuming code directly, outside any Flask request
context, with a fresh mongomock db per test, all in the same OS thread. A
threading.local cache would leak the first test's district_defs into every
later test in the same run.
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
    def test_falls_back_to_a_fresh_uncached_query(self, monkeypatch):
        """Outside a Flask request (e.g. this test itself, or the session
        tick's background thread), every call re-queries — no thread-local
        cache, since Python's OS threads get reused across unrelated units
        of work (different tests, different tick runs) with no reliable
        invalidation signal available here."""
        calls = []

        class _FakeCollection:
            def find(self, *args, **kwargs):
                calls.append((args, kwargs))
                return [{"key": "courthouse"}]

        class _FakeDb:
            district_defs = _FakeCollection()

        fake_mongo = type("FakeMongo", (), {"db": _FakeDb()})()
        monkeypatch.setattr(fc, "mongo", fake_mongo)

        fc._get_cached_district_defs_by_key()
        fc._get_cached_district_defs_by_key()

        assert len(calls) == 2, "No Flask context — each call should query fresh, not leak a stale cache"
