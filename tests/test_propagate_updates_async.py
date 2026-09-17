"""
Tests for _dispatch_propagate_updates (helpers/change_helpers.py) — the
entry point every change-approval flow (approve_change, system_approve_change,
force_approve_change, system_force_approve_change, revert_change) now uses
instead of calling propagate_updates directly.

Background: approving an overlord nation's edit synchronously recalculates
every vassal (propagate_updates's dependent-object cascade) before the HTTP
response returns — already-documented at ~8s per dependent nation. Since
propagate_updates has no return value nothing downstream depends on, and
this codebase's one existing background-thread convention
(tick_helpers.py's run_tick_async + _run_tick_guarded: Thread(daemon=True)
+ print-a-traceback-on-failure) already establishes the pattern to reuse,
the cascade now runs in the background for interactive admin HTTP requests.

Two things make blindly backgrounding it unsafe, so _dispatch_propagate_updates
stays synchronous in both cases:
  - `session` set: a pymongo ClientSession can't cross threads, and it's
    tied to a transaction (the tick's deferred commit phase) that may
    already be committing/rolling back by the time a thread got to it.
  - no live Flask request context: a standalone script's process could
    exit before a background thread finishes, silently dropping the
    cascade — there's no HTTP response to speed up anyway.
"""
import threading
import time
from unittest.mock import patch

import helpers.change_helpers as ch


class TestDispatchStaysSyncWithSessionOrNoRequestContext:
    def test_session_present_runs_synchronously_even_inside_a_request(self, flask_app):
        """A session means a transaction — must never background this,
        even if a request context also happens to be present."""
        calls = []
        with patch.object(ch, "propagate_updates", side_effect=lambda *a, **k: calls.append(k.get("session"))):
            with flask_app.test_request_context("/"):
                ch._dispatch_propagate_updates("nations", "id1", {}, session="fake-session")
        assert calls == ["fake-session"]

    def test_no_request_context_runs_synchronously(self):
        """Outside any Flask request (a standalone script, or a background
        thread like the tick itself) — no HTTP response to speed up, and a
        script's process could exit before a thread finished."""
        calls = []
        with patch.object(ch, "propagate_updates", side_effect=lambda *a, **k: calls.append(1)):
            ch._dispatch_propagate_updates("nations", "id1", {})
        assert calls == [1]

    def test_synchronous_path_passes_through_all_arguments(self, flask_app):
        captured = {}

        def _fake(changed_data_type, changed_object_id, changed_object, reason, session=None, skip_ids=None):
            captured.update(locals())

        with patch.object(ch, "propagate_updates", side_effect=_fake):
            ch._dispatch_propagate_updates(
                "nations", "id1", {"name": "Test"}, reason="my reason",
                session="s", skip_ids={("nations", "id2")},
            )
        assert captured["changed_data_type"] == "nations"
        assert captured["changed_object_id"] == "id1"
        assert captured["reason"] == "my reason"
        assert captured["skip_ids"] == {("nations", "id2")}


class TestDispatchGoesAsyncForInteractiveRequests:
    def test_spawns_a_background_thread(self, flask_app):
        release = threading.Event()
        ran = threading.Event()

        def _fake(*a, **k):
            release.wait(timeout=2)
            ran.set()

        with patch.object(ch, "propagate_updates", side_effect=_fake):
            with flask_app.test_request_context("/"):
                ch._dispatch_propagate_updates("nations", "id-async-1", {})
            # The dispatch call must return immediately — propagate_updates
            # is still blocked on release, so it can't have run yet if this
            # really returned without waiting on it.
            assert not ran.is_set()
            release.set()

        assert ran.wait(timeout=2), "background propagate_updates never ran"

    def test_duplicate_in_flight_dispatch_is_skipped(self, flask_app):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def _fake(*a, **k):
            calls.append(1)
            started.set()
            release.wait(timeout=2)

        with patch.object(ch, "propagate_updates", side_effect=_fake):
            with flask_app.test_request_context("/"):
                ch._dispatch_propagate_updates("nations", "dup-id", {})
                assert started.wait(timeout=2)
                # Second dispatch for the SAME id while the first is still
                # running must be skipped, not queued as a second thread.
                ch._dispatch_propagate_updates("nations", "dup-id", {})

            release.set()
            time.sleep(0.05)

        assert calls == [1]

    def test_guard_is_released_after_success_so_a_later_dispatch_can_run(self, flask_app):
        calls = []

        def _fake(*a, **k):
            calls.append(1)

        with patch.object(ch, "propagate_updates", side_effect=_fake):
            with flask_app.test_request_context("/"):
                ch._dispatch_propagate_updates("nations", "seq-id", {})
            time.sleep(0.1)
            with flask_app.test_request_context("/"):
                ch._dispatch_propagate_updates("nations", "seq-id", {})
            time.sleep(0.1)

        assert calls == [1, 1]

    def test_guard_is_released_after_failure(self, flask_app):
        """A propagate_updates exception must not leave the in-flight guard
        stuck forever (permanently blocking future saves of that object)."""
        calls = []

        def _fake(*a, **k):
            calls.append(1)
            raise RuntimeError("boom")

        with patch.object(ch, "propagate_updates", side_effect=_fake):
            with flask_app.test_request_context("/"):
                ch._dispatch_propagate_updates("nations", "fail-id", {})
            time.sleep(0.1)
            with flask_app.test_request_context("/"):
                ch._dispatch_propagate_updates("nations", "fail-id", {})
            time.sleep(0.1)

        assert calls == [1, 1]
        assert ("nations", "fail-id") not in ch._propagating_ids
