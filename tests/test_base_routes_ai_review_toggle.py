"""Tests for the AI change-review admin toggles in routes/base_routes.py —
mirrors the existing (untested, but identically-shaped) toggle_revert_warning
route; these are new enough to be worth covering directly.
"""
import importlib
from unittest.mock import patch

import pytest

# routes/__init__.py does `from .base_routes import base_routes`, which
# rebinds the `base_routes` attribute on the `routes` package to the
# Blueprint object — so `import routes.base_routes as br` (attribute
# lookup through the package) resolves to the Blueprint, not the module.
# Pulling straight from sys.modules avoids that shadowing — see
# tests/test_change_approval_flash.py's identical note.
br = importlib.import_module("routes.base_routes")
import helpers.ai_review_helpers as arh


@pytest.fixture
def patch_toggle_routes(mock_mongo):
    with patch.object(br, "mongo", mock_mongo), patch.object(arh, "mongo", mock_mongo):
        br._ai_review_settings_cache.update({"enabled": True, "dry_run": True, "ts": 0.0})
        yield


class TestToggleAiReview:
    def test_non_admin_is_forbidden(self, flask_app, patch_toggle_routes):
        with flask_app.test_request_context("/admin/toggle-ai-review"):
            with patch.object(br, "g") as mock_g:
                mock_g.user = {"id": "u1", "is_admin": False}
                with pytest.raises(Exception):
                    br.toggle_ai_review()

    def test_flips_enabled_and_updates_cache(self, flask_app, patch_toggle_routes, test_db):
        with flask_app.test_request_context("/admin/toggle-ai-review"):
            with patch.object(br, "g") as mock_g:
                mock_g.user = {"id": "admin1", "is_admin": True}
                br.toggle_ai_review()

        stored = test_db["global_modifiers"].find_one({"name": "global_modifiers"})
        assert stored["ai_review_enabled"] is False   # started True (default), flipped
        assert br._ai_review_settings_cache["enabled"] is False
        assert br._ai_review_settings_cache["dry_run"] is True   # untouched

    def test_toggling_twice_returns_to_original_state(self, flask_app, patch_toggle_routes, test_db):
        with flask_app.test_request_context("/admin/toggle-ai-review"):
            with patch.object(br, "g") as mock_g:
                mock_g.user = {"id": "admin1", "is_admin": True}
                br.toggle_ai_review()
                br.toggle_ai_review()

        stored = test_db["global_modifiers"].find_one({"name": "global_modifiers"})
        assert stored["ai_review_enabled"] is True


class TestToggleAiReviewDryRun:
    def test_non_admin_is_forbidden(self, flask_app, patch_toggle_routes):
        with flask_app.test_request_context("/admin/toggle-ai-review-dry-run"):
            with patch.object(br, "g") as mock_g:
                mock_g.user = None
                with pytest.raises(Exception):
                    br.toggle_ai_review_dry_run()

    def test_flips_dry_run_only(self, flask_app, patch_toggle_routes, test_db):
        with flask_app.test_request_context("/admin/toggle-ai-review-dry-run"):
            with patch.object(br, "g") as mock_g:
                mock_g.user = {"id": "admin1", "is_admin": True}
                br.toggle_ai_review_dry_run()

        stored = test_db["global_modifiers"].find_one({"name": "global_modifiers"})
        assert stored["ai_review_dry_run"] is False
        assert "ai_review_enabled" not in stored   # untouched -- this toggle only ever sets dry_run
        assert br._ai_review_settings_cache["dry_run"] is False
        assert br._ai_review_settings_cache["enabled"] is True
