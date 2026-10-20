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


# ---------------------------------------------------------------------------
# Partial-approval "units" form field threading
#
# approve_change_route / force_approve_change_route / deny_change_route /
# revert_change_route are POST routes that read request.form.getlist("units")
# (the checked boxes from render_changes_preview's selectable=true mode) and
# pass them through as approve_change/etc.'s selected_paths kwarg — these
# exercise the real route functions against a POST request body to confirm
# that wiring, rather than just the underlying helpers.
# ---------------------------------------------------------------------------

class TestUnitsFormFieldThreadedToSelectedPaths:
    def test_approve_route_passes_checked_units_as_selected_paths(self, flask_app):
        change_id = ObjectId()
        with flask_app.test_request_context(
            f"/changes/item/{change_id}/approve", method="POST", data={"units": ["money", "wood"]},
        ):
            with patch("routes.change_routes.g") as mock_g, \
                 patch("routes.change_routes.approve_change", return_value=True) as mock_approve:
                mock_g.user = {"id": "admin1", "is_admin": True}
                cr.approve_change_route.__wrapped__(str(change_id))

        mock_approve.assert_called_once_with(change_id, selected_paths=["money", "wood"])

    def test_approve_route_with_no_units_passes_none(self, flask_app):
        """No checkboxes submitted (or none checked) -- the whole change,
        matching approve_change's own default."""
        change_id = ObjectId()
        with flask_app.test_request_context(f"/changes/item/{change_id}/approve", method="POST"):
            with patch("routes.change_routes.g") as mock_g, \
                 patch("routes.change_routes.approve_change", return_value=True) as mock_approve:
                mock_g.user = {"id": "admin1", "is_admin": True}
                cr.approve_change_route.__wrapped__(str(change_id))

        mock_approve.assert_called_once_with(change_id, selected_paths=None)

    def test_force_approve_route_passes_checked_units(self, flask_app):
        change_id = ObjectId()
        with flask_app.test_request_context(
            f"/changes/item/{change_id}/force-approve", method="POST", data={"units": ["stockpiles.stone"]},
        ):
            with patch("routes.change_routes.g") as mock_g, \
                 patch("routes.change_routes.force_approve_change", return_value=True) as mock_force:
                mock_g.user = {"id": "admin1", "is_admin": True}
                cr.force_approve_change_route.__wrapped__(str(change_id))

        mock_force.assert_called_once_with(change_id, selected_paths=["stockpiles.stone"])

    def test_deny_route_passes_checked_units(self, flask_app):
        change_id = ObjectId()
        with flask_app.test_request_context(
            f"/changes/item/{change_id}/deny", method="POST", data={"units": ["money"]},
        ):
            with patch("routes.change_routes.g") as mock_g, \
                 patch("routes.change_routes.deny_change", return_value=True) as mock_deny:
                mock_g.user = {"id": "admin1", "is_admin": True}
                cr.deny_change_route.__wrapped__(str(change_id))

        mock_deny.assert_called_once_with(change_id, selected_paths=["money"])

    def test_revert_route_passes_checked_units(self, flask_app):
        change_id = ObjectId()
        with flask_app.test_request_context(
            f"/changes/item/{change_id}/revert", method="POST", data={"units": ["wood"]},
        ):
            with patch("routes.change_routes.g") as mock_g, \
                 patch("routes.change_routes.revert_change", return_value=True) as mock_revert:
                mock_g.user = {"id": "admin1", "is_admin": True}
                cr.revert_change_route.__wrapped__(str(change_id))

        mock_revert.assert_called_once_with(change_id, selected_paths=["wood"])


# ---------------------------------------------------------------------------
# _attach_drift_conflicts — the pending-changes list pages' drift-warning
# wiring (see get_change_drift_conflicts). Most approve/deny actions happen
# from these list pages rather than the single-change page, so each row
# needs its own drift report computed before rendering.
# ---------------------------------------------------------------------------

class TestAttachDriftConflicts:
    def test_attaches_a_report_per_change(self):
        changes = [{"_id": ObjectId()}, {"_id": ObjectId()}]
        with patch("routes.change_routes.get_change_drift_conflicts", side_effect=[[], ["conflict"]]) as mock_fn:
            cr._attach_drift_conflicts(changes)

        assert mock_fn.call_count == 2
        assert changes[0]["_drift_conflicts"] == []
        assert changes[1]["_drift_conflicts"] == ["conflict"]

    def test_empty_list_is_a_no_op(self):
        with patch("routes.change_routes.get_change_drift_conflicts") as mock_fn:
            cr._attach_drift_conflicts([])
        mock_fn.assert_not_called()
