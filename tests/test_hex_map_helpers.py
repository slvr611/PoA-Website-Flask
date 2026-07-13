"""
Tests for helpers/hex_map_helpers.py's map snapshot creation tools:
snapshot_current_map, get_session_tiles, get_all_tiles, and
_get_nation_colors_snapshot.

These guard against the exact failure mode that let Session 76's stored map
snapshot silently go stale for months (never refreshed after May 2026, while
the live map kept changing underneath it) — only noticed and recovered from
a database backup much later. This suite exercises the actual insert/update
path so a regression there gets caught immediately instead of drifting
silently again.
"""
import pytest
from unittest.mock import patch, MagicMock

import helpers.hex_map_helpers as hmh


@pytest.fixture
def patch_hex_helpers(test_db):
    """Patch hex_map_helpers.mongo to use the isolated in-memory database."""
    m = MagicMock()
    m.db = test_db
    with patch("helpers.hex_map_helpers.mongo", m):
        yield test_db


def _insert_tile(db, q, r, **extra):
    doc = {"q": q, "r": r, **extra}
    db["hex_map_tiles"].insert_one(doc)
    return doc


class TestGetAllTiles:
    def test_returns_tiles_with_projection_fields_only(self, patch_hex_helpers):
        db = patch_hex_helpers
        _insert_tile(db, 0, 0, terrain="plains", owner="Testland", extra_field="should not appear")

        tiles = hmh.get_all_tiles()

        assert len(tiles) == 1
        t = tiles[0]
        assert t["q"] == 0 and t["r"] == 0
        assert t["terrain"] == "plains"
        assert t["owner"] == "Testland"
        assert "extra_field" not in t
        assert "_id" not in t

    def test_empty_collection_returns_empty_list(self, patch_hex_helpers):
        assert hmh.get_all_tiles() == []


class TestGetNationColorsSnapshot:
    def test_uses_accent_color_when_present(self, patch_hex_helpers):
        db = patch_hex_helpers
        db["nations"].insert_one({"name": "Redland", "accent_color": "#ff0000"})

        colors = hmh._get_nation_colors_snapshot()

        assert colors["Redland"] == "#ff0000"

    def test_falls_back_to_deterministic_color_when_missing(self, patch_hex_helpers):
        db = patch_hex_helpers
        db["nations"].insert_one({"name": "Colorless"})

        colors = hmh._get_nation_colors_snapshot()

        assert colors["Colorless"] == hmh.name_to_color("Colorless")

    def test_nations_without_a_name_are_skipped(self, patch_hex_helpers):
        db = patch_hex_helpers
        db["nations"].insert_one({"accent_color": "#123456"})

        colors = hmh._get_nation_colors_snapshot()

        assert colors == {}


class TestSnapshotCurrentMap:
    """snapshot_current_map(session_num) — the tool this whole suite exists
    to protect: it must faithfully copy the CURRENT live tiles/grid config/
    nation colors into hex_map_history, upserted by session number."""

    def test_inserts_new_snapshot_when_none_exists(self, patch_hex_helpers):
        db = patch_hex_helpers
        _insert_tile(db, 1, 1, terrain="forest", owner="Woodland")
        db["nations"].insert_one({"name": "Woodland", "accent_color": "#00ff00"})
        db["global_modifiers"].insert_one({
            "name": "hex_map_config", "cols": 50, "rows": 40, "hex_size": 45,
        })

        hmh.snapshot_current_map(5)

        snap = db["hex_map_history"].find_one({"session": 5})
        assert snap is not None
        assert len(snap["tiles"]) == 1
        assert snap["tiles"][0]["owner"] == "Woodland"
        assert snap["cols"] == 50 and snap["rows"] == 40 and snap["hex_size"] == 45
        assert snap["nation_colors"]["Woodland"] == "#00ff00"
        assert "created_at" in snap

    def test_updates_existing_snapshot_in_place_not_duplicated(self, patch_hex_helpers):
        db = patch_hex_helpers
        db["hex_map_history"].insert_one({
            "session": 5, "tiles": [{"q": 0, "r": 0, "terrain": "stale"}],
            "cols": 10, "rows": 10, "hex_size": 20, "nation_colors": {},
            "created_at": "old-timestamp",
        })
        _insert_tile(db, 2, 2, terrain="mountain", owner="Rockland")
        db["global_modifiers"].insert_one({
            "name": "hex_map_config", "cols": 99, "rows": 88, "hex_size": 77,
        })

        hmh.snapshot_current_map(5)

        assert db["hex_map_history"].count_documents({"session": 5}) == 1
        snap = db["hex_map_history"].find_one({"session": 5})
        assert snap["created_at"] == "old-timestamp"  # preserved, not clobbered
        assert "updated_at" in snap
        assert snap["cols"] == 99
        assert len(snap["tiles"]) == 1
        assert snap["tiles"][0]["owner"] == "Rockland"

    def test_snapshot_does_not_change_after_a_later_live_edit(self, patch_hex_helpers):
        """Regression guard for the exact bug that went unnoticed for months:
        a snapshot must capture tiles as they are AT CALL TIME, and a later
        live-tile edit must never retroactively alter an already-taken
        snapshot (Session 76 only looked "current" because nobody checked
        it against fresh edits for two months)."""
        db = patch_hex_helpers
        _insert_tile(db, 3, 3, terrain="plains", owner="Original")
        db["global_modifiers"].insert_one({"name": "hex_map_config"})

        hmh.snapshot_current_map(10)
        db["hex_map_tiles"].update_one({"q": 3, "r": 3}, {"$set": {"owner": "NewOwner"}})

        snap = db["hex_map_history"].find_one({"session": 10})
        assert snap["tiles"][0]["owner"] == "Original"

    def test_missing_hex_map_config_falls_back_to_defaults(self, patch_hex_helpers):
        db = patch_hex_helpers

        hmh.snapshot_current_map(1)

        snap = db["hex_map_history"].find_one({"session": 1})
        assert snap["cols"] == 20
        assert snap["rows"] == 15
        assert snap["hex_size"] == 40

    def test_return_message_reports_tile_count(self, patch_hex_helpers):
        db = patch_hex_helpers
        _insert_tile(db, 0, 0)
        _insert_tile(db, 1, 0)
        db["global_modifiers"].insert_one({"name": "hex_map_config"})

        message = hmh.snapshot_current_map(3)

        assert "session 3" in message
        assert "2" in message


class TestGetSessionTiles:
    def test_returns_snapshot_tiles_for_a_historical_session(self, patch_hex_helpers):
        db = patch_hex_helpers
        db["hex_map_history"].insert_one({
            "session": 42, "tiles": [{"q": 5, "r": 5, "terrain": "swamp"}],
        })
        _insert_tile(db, 9, 9, terrain="plains")  # live tiles differ from the snapshot

        tiles = hmh.get_session_tiles(42)

        assert tiles == [{"q": 5, "r": 5, "terrain": "swamp"}]

    def test_falls_back_to_live_tiles_when_no_snapshot_exists(self, patch_hex_helpers):
        db = patch_hex_helpers
        _insert_tile(db, 1, 1, terrain="plains", owner="Liveland")

        tiles = hmh.get_session_tiles(999)

        assert len(tiles) == 1
        assert tiles[0]["owner"] == "Liveland"
