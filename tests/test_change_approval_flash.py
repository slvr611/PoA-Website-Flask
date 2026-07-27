"""
Regression test for a bug in the pending-changes approve/deny/force-approve
routes: approve_change/force_approve_change/deny_change return False (without
raising) when they can't apply the change — e.g. check_no_other_changes fails
because the target was modified since the request was made — and already
flash their own specific "error" message in that case. The routes, however,
flashed a hardcoded "Change #X has been approved." success message right
after calling the function *unconditionally*, regardless of its return value.
So a failed approval showed BOTH the real error and a false "has been
approved" success message, even though the change was left "Pending".

This exercises the real route view functions (bypassing the admin_required
decorator via __wrapped__, since it only checks g.user) with approve_change/
force_approve_change/deny_change mocked to return False, and asserts only
the "error" category flash appears — never the success one.
"""
import importlib
from unittest.mock import patch
from bson import ObjectId

# routes/__init__.py does `from .change_routes import change_routes`, which
# rebinds the `change_routes` attribute on the `routes` package to the
# Blueprint object — so `import routes.change_routes as cr` (attribute
# lookup through the package) resolves to the Blueprint, not the module.
# Pulling straight from sys.modules avoids that shadowing.
cr = importlib.import_module("routes.change_routes")


class TestApproveChangeRouteDoesNotFalselyFlashSuccess:
    def test_failed_approve_shows_only_error(self, flask_app):
        change_id = ObjectId()
        with flask_app.test_request_context(f"/changes/item/{change_id}/approve"):
            with patch("routes.change_routes.g") as mock_g, \
                 patch("routes.change_routes.approve_change", return_value=False) as mock_approve:
                mock_g.user = {"id": "admin1", "is_admin": True}
                cr.approve_change_route.__wrapped__(str(change_id))
                mock_approve.assert_called_once()

            from flask import get_flashed_messages
            messages = get_flashed_messages(with_categories=True)
            categories = [c for c, _ in messages]
            assert "success" not in categories, (
                f"approve_change returned False but a success flash was shown: {messages}"
            )

    def test_successful_approve_shows_success(self, flask_app):
        change_id = ObjectId()
        with flask_app.test_request_context(f"/changes/item/{change_id}/approve"):
            with patch("routes.change_routes.g") as mock_g, \
                 patch("routes.change_routes.approve_change", return_value=True):
                mock_g.user = {"id": "admin1", "is_admin": True}
                cr.approve_change_route.__wrapped__(str(change_id))

            from flask import get_flashed_messages
            messages = get_flashed_messages(with_categories=True)
            assert ("success", f"Change #{change_id} has been approved.") in messages

    def test_failed_force_approve_shows_only_error(self, flask_app):
        change_id = ObjectId()
        with flask_app.test_request_context(f"/changes/item/{change_id}/force-approve"):
            with patch("routes.change_routes.g") as mock_g, \
                 patch("routes.change_routes.force_approve_change", return_value=False):
                mock_g.user = {"id": "admin1", "is_admin": True}
                cr.force_approve_change_route.__wrapped__(str(change_id))

            from flask import get_flashed_messages
            messages = get_flashed_messages(with_categories=True)
            categories = [c for c, _ in messages]
            assert "success" not in categories

    def test_failed_deny_shows_error(self, flask_app):
        change_id = ObjectId()
        with flask_app.test_request_context(f"/changes/item/{change_id}/deny"):
            with patch("routes.change_routes.g") as mock_g, \
                 patch("routes.change_routes.deny_change", return_value=False):
                mock_g.user = {"id": "admin1", "is_admin": True}
                cr.deny_change_route.__wrapped__(str(change_id))

            from flask import get_flashed_messages
            messages = get_flashed_messages(with_categories=True)
            categories = [c for c, _ in messages]
            assert "success" not in categories
            assert "error" in categories
