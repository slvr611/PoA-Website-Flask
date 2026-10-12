"""
Regression tests for the 2026-10-12 report: historical map views (viewing
a past session via /api/hex-map/tiles/<session_num>) showed TODAY's
vassal/overlord relationships rather than whatever was true during that
session, because nothing snapshotted nation_overlords at all — the map
always fell back to a live, session-unaware fetch.

Fixed by:
  - helpers/hex_map_helpers.py's snapshot_current_map now saves
    nation_overlords (via _get_nation_overlords_snapshot) alongside tiles
    and nation_colors.
  - routes/hex_map_routes.py's hex_map_tiles_session merges saved overlords
    (session-accurate) with live ones (fallback only for a nation entirely
    missing from an OLDER snapshot taken before this field existed).

The critical subtlety both of these share: an independent nation must be
recorded as "" (present, no overlord) rather than omitted — otherwise the
live-data fallback can't distinguish "had no overlord back then" from
"didn't exist in this snapshot", and would incorrectly show today's
relationship for any nation that's since become or stopped being a vassal.
"""
import importlib
from unittest.mock import MagicMock, patch

hmr = importlib.import_module("routes.hex_map_routes")


def _patch_mongo(test_db):
    fake_mongo = MagicMock()
    fake_mongo.db = test_db
    return patch.object(hmr, "mongo", fake_mongo)


class TestGetNationOverlordsLive:
    def test_every_nation_present_vassal_resolved_independent_empty(self, test_db):
        overlord_id = test_db["nations"].insert_one({"name": "Overlordia"}).inserted_id
        test_db["nations"].insert_one({"name": "Vassalia", "overlord": str(overlord_id)})
        test_db["nations"].insert_one({"name": "Freeland"})

        with _patch_mongo(test_db):
            result = hmr._get_nation_overlords()

        assert result == {"Overlordia": "", "Vassalia": "Overlordia", "Freeland": ""}


class TestHexMapTilesSessionOverlordMerge:
    def _config(self, test_db):
        test_db["global_modifiers"].insert_one({"name": "hex_map_config"})

    def test_saved_overlord_relationship_wins_over_live(self, flask_app, test_db):
        """The core scenario: Vassalia was independent back at session 5,
        but has since become Overlordia's vassal. The historical view must
        show it as independent, not as a vassal."""
        self._config(test_db)
        overlord_id = test_db["nations"].insert_one({"name": "Overlordia"}).inserted_id
        test_db["nations"].insert_one({"name": "Vassalia", "overlord": str(overlord_id)})  # LIVE: now a vassal
        test_db["hex_map_history"].insert_one({
            "session": 5, "tiles": [],
            "nation_colors": {}, "nation_overlords": {"Overlordia": "", "Vassalia": ""},  # back then: independent
        })

        with _patch_mongo(test_db), flask_app.test_request_context():
            resp = hmr.hex_map_tiles_session(5)
            data = resp.get_json()

        assert data["nation_overlords"]["Vassalia"] == ""

    def test_saved_vassal_relationship_shows_even_if_independent_now(self, flask_app, test_db):
        """The mirror case: Vassalia WAS a vassal at session 5, but is
        independent today. The historical view must still show the old
        overlord, not today's independence."""
        self._config(test_db)
        test_db["nations"].insert_one({"name": "Overlordia"})
        test_db["nations"].insert_one({"name": "Vassalia"})  # LIVE: independent now
        test_db["hex_map_history"].insert_one({
            "session": 5, "tiles": [],
            "nation_colors": {}, "nation_overlords": {"Overlordia": "", "Vassalia": "Overlordia"},
        })

        with _patch_mongo(test_db), flask_app.test_request_context():
            resp = hmr.hex_map_tiles_session(5)
            data = resp.get_json()

        assert data["nation_overlords"]["Vassalia"] == "Overlordia"

    def test_nation_missing_from_older_snapshot_falls_back_to_live(self, flask_app, test_db):
        """A snapshot taken before this field existed (or before a nation
        was created) has no entry for that nation at all — must fall back
        to live data rather than showing nothing."""
        self._config(test_db)
        overlord_id = test_db["nations"].insert_one({"name": "Overlordia"}).inserted_id
        test_db["nations"].insert_one({"name": "NewNation", "overlord": str(overlord_id)})
        test_db["hex_map_history"].insert_one({
            "session": 5, "tiles": [],
            "nation_colors": {},
            # No "nation_overlords" key at all — an old, pre-feature snapshot.
        })

        with _patch_mongo(test_db), flask_app.test_request_context():
            resp = hmr.hex_map_tiles_session(5)
            data = resp.get_json()

        assert data["nation_overlords"]["NewNation"] == "Overlordia"

    def test_live_view_uses_live_overlords(self, flask_app, test_db):
        overlord_id = test_db["nations"].insert_one({"name": "Overlordia"}).inserted_id
        test_db["nations"].insert_one({"name": "Vassalia", "overlord": str(overlord_id)})
        test_db["global_modifiers"].insert_one({"name": "hex_map_config"})

        with _patch_mongo(test_db), flask_app.test_request_context():
            resp = hmr.hex_map_tiles_session(999)  # no snapshot exists for this session
            data = resp.get_json()

        assert data["nation_overlords"]["Vassalia"] == "Overlordia"
