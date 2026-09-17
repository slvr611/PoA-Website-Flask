"""Regression test for a real production incident (2026-09-17): a tick
failed outright with

    pymongo.errors.NetworkTimeout: ... The read operation timed out
    (configured timeouts: socketTimeoutMS: 30000.0ms, ...)

from `old_nations = list(nation_db.find().sort("name", ASCENDING))` — the
bulk fetch at the start of nation processing. The timeout itself was a
deliberate, working fix (see app_core.py's Mongo config comment: without
it, the same read had previously hung forever instead of raising anything
catchable) — but the bulk fetch wasn't wrapped in with_mongo_retry, so the
very first transient blip failed the whole tick immediately instead of
retrying. Every major per-category bulk fetch in tick()/era_tick() (and a
couple of standalone helpers) now goes through with_mongo_retry.

This exercises the real tick() function's nations bulk-fetch path end to
end: a mocked nations collection whose find() raises once before
succeeding must not fail the tick.
"""
from unittest.mock import MagicMock, patch

from pymongo.errors import NetworkTimeout

import helpers.tick_helpers as th
from app_core import category_data


class TestTickNationsBulkFetchRetriesOnTransientFailure:
    def test_tick_survives_one_transient_failure_fetching_nations(self):
        find_mock = MagicMock(
            side_effect=[
                NetworkTimeout("cluster0-shard-00-01...: read operation timed out"),
                MagicMock(sort=MagicMock(return_value=[])),
            ]
        )
        fake_nation_db = MagicMock(find=find_mock)
        fake_mongo = MagicMock()
        fake_mongo.db.__getitem__.return_value.find_one.return_value = None
        fake_mongo.db.changes.find.return_value = []  # archive_old_changes: nothing to archive

        original_nations_db = category_data["nations"]["database"]
        category_data["nations"]["database"] = fake_nation_db
        try:
            with patch.object(th, "mongo", fake_mongo), \
                 patch("helpers.archive_helpers.mongo", fake_mongo), \
                 patch.object(th, "with_mongo_retry", wraps=th.with_mongo_retry) as retry_spy, \
                 patch("app_core.time.sleep"):
                # Nation Job Cleanup Tick is pure in-memory computation (no
                # DB calls of its own) — selecting it is enough to make
                # tick() reach the nations bulk fetch without pulling in
                # any other category's setup. fake_mongo.db.changes.find()
                # returns a MagicMock (falsy-empty-ish iterable via
                # find_one), so archive_old_changes finds nothing to do.
                result = th.tick({"run_Nation Job Cleanup Tick": "on"})
        finally:
            category_data["nations"]["database"] = original_nations_db

        assert find_mock.call_count == 2, "expected exactly one retry after the transient failure"
        assert retry_spy.call_count >= 1
        assert "FAILED" not in result

    def test_tick_fails_cleanly_when_every_attempt_times_out(self):
        """Not a hang, not a silent success — a real, reported failure once
        the retry budget is exhausted."""
        find_mock = MagicMock(side_effect=NetworkTimeout("still down"))
        fake_nation_db = MagicMock(find=find_mock)
        fake_mongo = MagicMock()
        fake_mongo.db.__getitem__.return_value.find_one.return_value = None

        original_nations_db = category_data["nations"]["database"]
        category_data["nations"]["database"] = fake_nation_db
        try:
            with patch.object(th, "mongo", fake_mongo), \
                 patch("app_core.time.sleep"):
                try:
                    th.tick({"run_Nation Job Cleanup Tick": "on"}, )
                    assert False, "expected NetworkTimeout to propagate out of tick()"
                except NetworkTimeout:
                    pass
        finally:
            category_data["nations"]["database"] = original_nations_db

        assert find_mock.call_count == 100, "expected the full retry budget to be used before giving up"
