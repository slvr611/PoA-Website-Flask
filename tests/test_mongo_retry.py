"""Tests for app_core.with_mongo_retry.

Context: a session tick hung for 8+ minutes on a single trade_routes.
update_one() call (2026-09-17) — the app's shared MongoDB client had no
socketTimeoutMS configured, so a socket read that never got a response
blocked forever instead of raising anything catchable. Two fixes:
1. The client now has explicit, finite timeouts (see app_core.py's Mongo
   config comment) so a hung operation fails instead of hanging forever.
2. with_mongo_retry wraps a single Mongo call and retries it up to 100
   times (by default) on any PyMongoError before giving up, printing each
   failed attempt to the console and making the final failure visible
   (flashed in a Flask request, or left to propagate so a tick's own
   _run_tick_guarded turns it into a tick summary).
"""
from unittest.mock import patch, MagicMock

from pymongo.errors import AutoReconnect, NetworkTimeout

import app_core


class TestWithMongoRetrySuccessPaths:
    def test_succeeds_on_first_attempt_without_sleeping(self):
        func = MagicMock(return_value="ok")
        with patch.object(app_core.time, "sleep") as sleep:
            result = app_core.with_mongo_retry(func, "arg1", kw="v")
        assert result == "ok"
        func.assert_called_once_with("arg1", kw="v")
        sleep.assert_not_called()

    def test_retries_then_succeeds(self):
        func = MagicMock(side_effect=[AutoReconnect("blip"), AutoReconnect("blip2"), "ok"])
        with patch.object(app_core.time, "sleep") as sleep:
            result = app_core.with_mongo_retry(func, max_attempts=5, delay_seconds=1)
        assert result == "ok"
        assert func.call_count == 3
        assert sleep.call_count == 2
        sleep.assert_called_with(1)


class TestWithMongoRetryGivesUp:
    def test_raises_the_last_error_after_exhausting_attempts(self):
        func = MagicMock(side_effect=NetworkTimeout("still down"))
        with patch.object(app_core.time, "sleep"):
            try:
                app_core.with_mongo_retry(func, max_attempts=3, delay_seconds=0)
                assert False, "expected NetworkTimeout to propagate"
            except NetworkTimeout:
                pass
        assert func.call_count == 3

    def test_prints_every_failed_attempt(self, capsys):
        func = MagicMock(side_effect=AutoReconnect("down"))
        with patch.object(app_core.time, "sleep"):
            try:
                app_core.with_mongo_retry(func, max_attempts=3, delay_seconds=0, description="test op")
            except AutoReconnect:
                pass
        out = capsys.readouterr().out
        assert out.count("test op failed (attempt") == 3
        assert "test op failed after 3 attempts" in out

    def test_flashes_the_final_failure_inside_a_request_context(self, flask_app):
        func = MagicMock(side_effect=AutoReconnect("down"))
        with flask_app.test_request_context("/"):
            with patch.object(app_core.time, "sleep"):
                try:
                    app_core.with_mongo_retry(func, max_attempts=2, delay_seconds=0, description="test op")
                except AutoReconnect:
                    pass
            from flask import get_flashed_messages
            messages = get_flashed_messages()
        assert any("test op failed after 2 attempts" in m for m in messages)

    def test_does_not_raise_when_no_request_context_exists(self):
        """Outside a request (e.g. the tick's background thread), there's
        nothing to flash to — must fall through cleanly and still raise
        the original error, not some new error about a missing context."""
        func = MagicMock(side_effect=AutoReconnect("down"))
        with patch.object(app_core.time, "sleep"):
            try:
                app_core.with_mongo_retry(func, max_attempts=2, delay_seconds=0)
                assert False, "expected AutoReconnect to propagate"
            except AutoReconnect:
                pass


class TestWithMongoRetryDoesNotRetryUnrelatedErrors:
    def test_non_pymongo_exceptions_propagate_immediately(self):
        func = MagicMock(side_effect=ValueError("not a mongo problem"))
        with patch.object(app_core.time, "sleep") as sleep:
            try:
                app_core.with_mongo_retry(func, max_attempts=5, delay_seconds=1)
                assert False, "expected ValueError to propagate"
            except ValueError:
                pass
        func.assert_called_once()
        sleep.assert_not_called()


class TestMongoClientHasFiniteTimeouts:
    def test_socket_timeout_is_configured(self):
        """The exact gap that caused the 2026-09-17 hang: without this, a
        stuck socket read blocks forever instead of raising anything
        with_mongo_retry (or anything else) could ever catch."""
        pool_options = app_core.mongo.cx.options.pool_options
        assert pool_options.socket_timeout is not None
        assert pool_options.socket_timeout > 0
