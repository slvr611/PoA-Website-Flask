"""Tests for get_nations_within_distance's migration to the chunk cache.

Context: this function used to run two separate hex_map_tiles queries —
one filtered to the acting nation's own tiles, one to nearly every OTHER
tile on the map (`{"owner": {"$nin": [None, "", nation_name]}}`, matching
almost the whole map on a populated world). Both cost the same several
seconds on production regardless of projection (aggregate transfer/server
processing, not round-trip count — see helpers/hex_map_helpers.py's module
comment), and this function can be called once per nation per mech RP
attempt needing a foreign conversion target — see
_best_foreign_conversion_target's docstring. It now reads the whole map
once through get_all_tiles_from_chunks (the chunk-backed, process-wide
cache) instead.
"""
from unittest.mock import MagicMock, patch

import helpers.hex_map_helpers as hmh


def _patch_mongo(test_db):
    m = MagicMock()
    m.db = test_db
    return patch.object(hmh, "mongo", m)


def _insert_tile(db, q, r, owner=None):
    db["hex_map_tiles"].insert_one({"q": q, "r": r, "owner": owner})


class TestGetNationsWithinDistance:
    def test_returns_nations_within_range_excludes_self_and_far_away(self, test_db):
        _insert_tile(test_db, 0, 0, owner="Home Nation")
        _insert_tile(test_db, 1, 0, owner="Home Nation")
        _insert_tile(test_db, 2, 0, owner="Nearby Nation")   # distance 1 from (1,0)
        _insert_tile(test_db, 50, 0, owner="Far Nation")     # way out of range
        _insert_tile(test_db, 3, 0, owner=None)              # unowned, ignored
        _insert_tile(test_db, 0, 1, owner="Home Nation")     # more of the caller's own territory

        with _patch_mongo(test_db):
            result = hmh.get_nations_within_distance("Home Nation", max_distance=3)

        assert result == ["Nearby Nation"]

    def test_empty_when_the_nation_owns_no_tiles(self, test_db):
        _insert_tile(test_db, 0, 0, owner="Someone Else")

        with _patch_mongo(test_db):
            result = hmh.get_nations_within_distance("Home Nation", max_distance=10)

        assert result == []

    def test_multiple_nearby_nations_returned_sorted(self, test_db):
        _insert_tile(test_db, 0, 0, owner="Home Nation")
        _insert_tile(test_db, 1, 0, owner="Zeta")
        _insert_tile(test_db, -1, 0, owner="Alpha")

        with _patch_mongo(test_db):
            result = hmh.get_nations_within_distance("Home Nation", max_distance=5)

        assert result == ["Alpha", "Zeta"]

    def test_reads_the_map_once_through_the_chunk_cache(self, test_db):
        """Regression guard: must not fall back to two separate broad
        hex_map_tiles queries (own tiles + nearly-every-other-tile)."""
        _insert_tile(test_db, 0, 0, owner="Home Nation")
        _insert_tile(test_db, 1, 0, owner="Nearby Nation")

        with _patch_mongo(test_db), \
             patch.object(hmh, "get_all_tiles_from_chunks", wraps=hmh.get_all_tiles_from_chunks) as spy:
            hmh.get_nations_within_distance("Home Nation", max_distance=5)

        spy.assert_called_once()
