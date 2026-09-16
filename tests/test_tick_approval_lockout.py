"""
Tests for the tick approval lockout: while a session/era tick is running
(tick_status.running, set by helpers/tick_helpers.py's _run_tick_guarded),
players can still REQUEST changes but admins cannot APPROVE them —
approve_change/force_approve_change must refuse and leave the change
untouched. Requesting is a structurally separate, insert-only code path
(request_change/system_request_change) that this lockout never touches.
"""
from unittest.mock import MagicMock, patch

import mongomock
from bson import ObjectId

import helpers.change_helpers as ch


def _fake_mongo(test_db):
    m = MagicMock()
    m.db = test_db
    return m


class TestIsTickLocked:
    def test_false_when_no_tick_status_document_exists(self):
        test_db = mongomock.MongoClient()["poa_test"]
        with patch.object(ch, "mongo", _fake_mongo(test_db)):
            assert ch.is_tick_locked() is False

    def test_true_while_tick_status_running_is_true(self):
        test_db = mongomock.MongoClient()["poa_test"]
        test_db["tick_status"].insert_one({"_id": "current", "running": True})
        with patch.object(ch, "mongo", _fake_mongo(test_db)):
            assert ch.is_tick_locked() is True

    def test_false_once_tick_status_running_is_false(self):
        test_db = mongomock.MongoClient()["poa_test"]
        test_db["tick_status"].insert_one({"_id": "current", "running": False})
        with patch.object(ch, "mongo", _fake_mongo(test_db)):
            assert ch.is_tick_locked() is False


class TestApproveChangeRespectsTheLockout:
    def test_refuses_and_flashes_when_locked(self, flask_app):
        test_db = mongomock.MongoClient()["poa_test"]
        test_db["players"].insert_one({"id": "admin1", "is_admin": True})
        test_db["tick_status"].insert_one({"_id": "current", "running": True})
        change_id = ObjectId()

        with flask_app.test_request_context():
            with patch.object(ch, "mongo", _fake_mongo(test_db)), \
                 patch.object(ch, "g") as mock_g:
                mock_g.user = {"id": "admin1"}
                result = ch.approve_change(change_id)

            from flask import get_flashed_messages
            messages = get_flashed_messages(with_categories=True)

        assert result is None
        assert any("tick is currently running" in msg.lower() for _, msg in messages)

    def test_admin_check_still_wins_over_the_lockout(self, flask_app):
        """A non-admin gets the "must be an admin" message, not the tick
        one — the lockout must not leak past who's even allowed to try."""
        test_db = mongomock.MongoClient()["poa_test"]
        test_db["players"].insert_one({"id": "notadmin", "is_admin": False})
        test_db["tick_status"].insert_one({"_id": "current", "running": True})
        change_id = ObjectId()

        with flask_app.test_request_context():
            with patch.object(ch, "mongo", _fake_mongo(test_db)), \
                 patch.object(ch, "g") as mock_g:
                mock_g.user = {"id": "notadmin"}
                result = ch.approve_change(change_id)

            from flask import get_flashed_messages
            messages = get_flashed_messages(with_categories=True)

        assert result is None
        assert any("must be an admin" in msg.lower() for _, msg in messages)
        assert not any("tick is currently running" in msg.lower() for _, msg in messages)

    def test_not_blocked_when_no_tick_is_running(self, flask_app):
        """Sanity check that the lockout only fires when actually locked —
        proceeds past the gate (and on into real approval logic, which will
        fail here on a nonexistent change, proving the gate itself passed)."""
        test_db = mongomock.MongoClient()["poa_test"]
        test_db["players"].insert_one({"id": "admin1", "is_admin": True})
        change_id = ObjectId()

        with flask_app.test_request_context():
            with patch.object(ch, "mongo", _fake_mongo(test_db)), \
                 patch.object(ch, "g") as mock_g:
                mock_g.user = {"id": "admin1"}
                try:
                    ch.approve_change(change_id)
                except Exception:
                    pass  # expected: no such change — proves we got past the lockout

            from flask import get_flashed_messages
            messages = get_flashed_messages(with_categories=True)

        assert not any("tick is currently running" in msg.lower() for _, msg in messages)


class TestForceApproveChangeRespectsTheLockout:
    def test_refuses_and_flashes_when_locked(self, flask_app):
        test_db = mongomock.MongoClient()["poa_test"]
        test_db["players"].insert_one({"id": "admin1", "is_admin": True})
        test_db["tick_status"].insert_one({"_id": "current", "running": True})
        change_id = ObjectId()

        with flask_app.test_request_context():
            with patch.object(ch, "mongo", _fake_mongo(test_db)), \
                 patch.object(ch, "g") as mock_g:
                mock_g.user = {"id": "admin1"}
                result = ch.force_approve_change(change_id)

            from flask import get_flashed_messages
            messages = get_flashed_messages(with_categories=True)

        assert result is None
        assert any("tick is currently running" in msg.lower() for _, msg in messages)


class TestNationJobsSelfApproveRespectsTheLockout:
    """routes/nation_routes.py's nation_edit_jobs_approve (bound to
    POST /nations/edit_jobs/<item_ref>/save) is the one non-admin,
    owner-initiated path in the codebase that calls system_approve_change
    directly, bypassing approve_change (and its lockout check) entirely —
    it must still save the request as Pending during a tick instead of
    silently auto-approving. Invokes the real view function (via
    __wrapped__, same technique as test_change_approval_flash.py, to skip
    owner_required's own DB-backed ownership check) with just
    request_change/system_approve_change/form_generator/get_data_on_item
    mocked, so the is_tick_locked() branch itself runs for real."""

    def _fake_form(self):
        form = MagicMock()
        form.validate.return_value = True
        form.data = {"csrf_token": "x", "submit": "Save"}
        return form

    def test_locked_skips_system_approve_and_flashes_pending(self, flask_app):
        import importlib
        nr = importlib.import_module("routes.nation_routes")

        with flask_app.test_request_context(
            "/nations/edit_jobs/Testland/save", method="POST"
        ):
            with patch.object(nr, "get_data_on_item", return_value=({}, None, {"_id": "n1", "name": "Testland"})), \
                 patch.object(nr.form_generator, "get_form", return_value=self._fake_form()), \
                 patch.object(nr, "request_change", return_value="change-1") as mock_request, \
                 patch.object(nr, "system_approve_change") as mock_approve, \
                 patch.object(nr, "is_tick_locked", return_value=True):
                nr.nation_edit_jobs_approve.__wrapped__("Testland")

            from flask import get_flashed_messages
            messages = get_flashed_messages(with_categories=True)

        mock_request.assert_called_once()
        mock_approve.assert_not_called()
        assert any("will need to be approved once it finishes" in msg for _, msg in messages)

    def test_unlocked_still_auto_approves_as_before(self, flask_app):
        import importlib
        nr = importlib.import_module("routes.nation_routes")

        with flask_app.test_request_context(
            "/nations/edit_jobs/Testland/save", method="POST"
        ):
            with patch.object(nr, "get_data_on_item", return_value=({}, None, {"_id": "n1", "name": "Testland"})), \
                 patch.object(nr.form_generator, "get_form", return_value=self._fake_form()), \
                 patch.object(nr, "request_change", return_value="change-1"), \
                 patch.object(nr, "system_approve_change", return_value=True) as mock_approve, \
                 patch.object(nr, "is_tick_locked", return_value=False):
                nr.nation_edit_jobs_approve.__wrapped__("Testland")

            from flask import get_flashed_messages
            messages = get_flashed_messages(with_categories=True)

        mock_approve.assert_called_once_with("change-1")
        assert any(msg == "Change request #change-1 created and approved." for _, msg in messages)
