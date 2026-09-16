"""Tests for the tick_status heartbeat added to _run_tick_guarded.

Context: a tick got stuck for ~7.5 hours (2026-09-15/16) and produced no
summary at all — success or failure — so there was no way to tell it was
even still running, let alone which step it had reached. Heroku's log
buffer only covers roughly the last hour, so the `print(label)` calls that
already existed per tick-function weren't enough either: by the time
anyone investigated, that output was long gone. _tick_heartbeat_start/
_log_tick_step/_tick_heartbeat_end persist the same "which step, since
when" information into a `tick_status` document instead, so it survives
indefinitely and is queryable at any time, hung tick or not.
"""
from unittest.mock import patch
import datetime

import helpers.tick_helpers as th


def _status(mock_mongo):
    return mock_mongo.db.tick_status.find_one({"_id": "current"})


class TestTickHeartbeatStart:
    def test_marks_running_with_tick_type_and_starting_step(self, mock_mongo):
        with patch("helpers.tick_helpers.mongo", mock_mongo):
            th._tick_heartbeat_start("Tick")

        doc = _status(mock_mongo)
        assert doc["running"] is True
        assert doc["tick_type"] == "Tick"
        assert doc["current_step"] == "(starting)"
        assert isinstance(doc["tick_started_at"], datetime.datetime)

    def test_never_raises_if_the_write_fails(self, mock_mongo):
        with patch("helpers.tick_helpers.mongo", mock_mongo), \
             patch.object(mock_mongo.db, "tick_status") as broken_collection:
            broken_collection.update_one.side_effect = RuntimeError("mongo down")
            th._tick_heartbeat_start("Tick")  # must not raise


class TestLogTickStep:
    def test_updates_current_step_and_leaves_running_true(self, mock_mongo, capsys):
        with patch("helpers.tick_helpers.mongo", mock_mongo):
            th._tick_heartbeat_start("Tick")
            th._log_tick_step("AI Decision Tick")

        doc = _status(mock_mongo)
        assert doc["current_step"] == "AI Decision Tick"
        assert doc["running"] is True  # still mid-tick
        assert "AI Decision Tick" in capsys.readouterr().out

    def test_never_raises_if_the_write_fails(self, mock_mongo):
        with patch("helpers.tick_helpers.mongo", mock_mongo), \
             patch.object(mock_mongo.db, "tick_status") as broken_collection:
            broken_collection.update_one.side_effect = RuntimeError("mongo down")
            th._log_tick_step("AI Decision Tick")  # must not raise


class TestTickHeartbeatEnd:
    def test_marks_not_running_with_result(self, mock_mongo):
        with patch("helpers.tick_helpers.mongo", mock_mongo):
            th._tick_heartbeat_start("Tick")
            th._tick_heartbeat_end("success")

        doc = _status(mock_mongo)
        assert doc["running"] is False
        assert doc["last_result"] == "success"
        assert isinstance(doc["last_finished_at"], datetime.datetime)


class TestRunTickGuardedMaintainsHeartbeat:
    def test_status_is_running_while_target_executes(self, mock_mongo):
        """Proves the heartbeat is visible WHILE the tick is still in
        progress (the exact thing missing during the stuck-tick incident) —
        not just before/after."""
        seen = {}

        def _target(form_data):
            seen["mid_run_status"] = dict(_status(mock_mongo))

        # _target isn't expected to raise, but if it ever does (a bug here,
        # an unpatched mock, whatever), _run_tick_guarded's except block
        # calls the REAL give_tick_summary — which, unmocked, writes local
        # files and uploads to the REAL S3 bucket using whatever credentials
        # happen to be in the environment. Patch it out unconditionally so a
        # test failure here can never have that side effect, regardless of
        # why _target failed.
        with patch("helpers.tick_helpers.mongo", mock_mongo), \
             patch.object(th, "give_tick_summary"):
            th._run_tick_guarded(_target, {}, "Tick")

        assert seen["mid_run_status"]["running"] is True
        assert seen["mid_run_status"]["tick_type"] == "Tick"

    def test_marks_success_after_a_clean_run(self, mock_mongo):
        with patch("helpers.tick_helpers.mongo", mock_mongo):
            th._run_tick_guarded(lambda form_data: None, {}, "Tick")

        doc = _status(mock_mongo)
        assert doc["running"] is False
        assert doc["last_result"] == "success"

    def test_marks_failed_on_exception(self, mock_mongo):
        def _target(form_data):
            raise RuntimeError("boom")

        with patch("helpers.tick_helpers.mongo", mock_mongo), \
             patch.object(th, "give_tick_summary"):
            th._run_tick_guarded(_target, {}, "Tick")

        doc = _status(mock_mongo)
        assert doc["running"] is False
        assert doc["last_result"] == "failed"

    def test_marks_failed_on_partial_commit_error(self, mock_mongo):
        def _target(form_data):
            raise th.TickPartialCommitError(RuntimeError("boom"), chunks_committed=2, items_committed=10)

        with patch("helpers.tick_helpers.mongo", mock_mongo), \
             patch.object(th, "give_tick_summary"):
            th._run_tick_guarded(_target, {}, "Tick")

        doc = _status(mock_mongo)
        assert doc["running"] is False
        assert doc["last_result"] == "failed"


def _global_modifiers(mock_mongo):
    return mock_mongo.db.global_modifiers.find_one({"name": "global_modifiers"})


class TestEnableRevertWarningAfterTick:
    """A tick finishing — win or lose — auto-enables the revert-warning
    banner (the automatic counterpart of routes/base_routes.py's manual
    toggle_revert_warning), at the same point tick_status.running is
    cleared, so it doubles as the change-approval lockout's release point
    (see helpers/change_helpers.py's is_tick_locked)."""

    def test_enabled_on_clean_success(self, mock_mongo):
        with patch("helpers.tick_helpers.mongo", mock_mongo):
            th._run_tick_guarded(lambda form_data: None, {}, "Tick")

        assert _global_modifiers(mock_mongo)["revert_warning"] is True

    def test_enabled_on_failure_too(self, mock_mongo):
        def _target(form_data):
            raise RuntimeError("boom")

        with patch("helpers.tick_helpers.mongo", mock_mongo), \
             patch.object(th, "give_tick_summary"):
            th._run_tick_guarded(_target, {}, "Tick")

        assert _global_modifiers(mock_mongo)["revert_warning"] is True

    def test_never_raises_if_the_write_fails(self, mock_mongo):
        with patch("helpers.tick_helpers.mongo", mock_mongo), \
             patch.object(mock_mongo.db, "global_modifiers") as broken_collection:
            broken_collection.update_one.side_effect = RuntimeError("mongo down")
            th._run_tick_guarded(lambda form_data: None, {}, "Tick")  # must not raise

    def test_upserts_when_no_document_exists_yet(self, mock_mongo):
        assert _global_modifiers(mock_mongo) is None
        with patch("helpers.tick_helpers.mongo", mock_mongo):
            th._enable_revert_warning_after_tick()

        assert _global_modifiers(mock_mongo)["revert_warning"] is True
