"""
Tests for explicit pair_id-based portal pairing — replaces the old
color-based pairing, which silently broke whenever a 3rd/4th tile
accidentally reused a color that was supposed to identify just one pair
(colors were deliberately reusable for visual variety, and
_build_portal_map only paired a color with exactly 2 tiles — see the
2026-10-11 Taika/Bloodlight Vigil report).

- helpers.trade_route_helpers._build_portal_map now groups by
  portal.pair_id instead of portal.color.
- routes.hex_map_routes._resolve_portal_link resolves a client-submitted
  {"link_to": {"q", "r"}} / None into the actual stored shape, handling the
  side effects of stealing a tile away from its previous partner.
"""
import importlib
from unittest.mock import MagicMock, patch

import helpers.trade_route_helpers as trh

hmr = importlib.import_module("routes.hex_map_routes")


def _patch_mongo(test_db):
    fake_mongo = MagicMock()
    fake_mongo.db = test_db
    return (
        patch.object(trh, "mongo", fake_mongo),
        patch.object(hmr, "mongo", fake_mongo),
    )


def _tile(test_db, q, r, pair_id=None, owner=None, **extra):
    doc = {"q": q, "r": r, "owner": owner}
    doc["portal"] = {"pair_id": pair_id} if pair_id else {}
    doc.update(extra)
    test_db["hex_map_tiles"].insert_one(doc)


class TestBuildPortalMapUsesPairId:
    def test_two_tiles_sharing_a_pair_id_are_paired(self, test_db):
        _tile(test_db, 0, 0, pair_id="abc123")
        _tile(test_db, 5, 5, pair_id="abc123")
        p1, p2 = _patch_mongo(test_db)
        with p1, p2:
            portal_map = trh._build_portal_map()
        assert portal_map.get((0, 0)) == (5, 5)
        assert portal_map.get((5, 5)) == (0, 0)

    def test_pair_id_shared_by_more_than_two_tiles_is_not_paired(self, test_db):
        """The exact scenario that broke color-based pairing — now it would
        require someone to literally assign the same pair_id to 3+ tiles by
        hand (impossible through the normal linking flow, which always
        generates a fresh id), but the safety net stays as defense in depth."""
        _tile(test_db, 0, 0, pair_id="shared")
        _tile(test_db, 5, 5, pair_id="shared")
        _tile(test_db, 9, 9, pair_id="shared")
        p1, p2 = _patch_mongo(test_db)
        with p1, p2:
            portal_map = trh._build_portal_map()
        assert portal_map == {}

    def test_unlinked_portal_tile_is_not_paired(self, test_db):
        _tile(test_db, 0, 0)  # portal: {} — no pair_id
        p1, p2 = _patch_mongo(test_db)
        with p1, p2:
            portal_map = trh._build_portal_map()
        assert portal_map == {}

    def test_different_pair_ids_do_not_cross_pair(self, test_db):
        _tile(test_db, 0, 0, pair_id="pair-a")
        _tile(test_db, 1, 1, pair_id="pair-a")
        _tile(test_db, 2, 2, pair_id="pair-b")
        _tile(test_db, 3, 3, pair_id="pair-b")
        p1, p2 = _patch_mongo(test_db)
        with p1, p2:
            portal_map = trh._build_portal_map()
        assert portal_map.get((0, 0)) == (1, 1)
        assert portal_map.get((2, 2)) == (3, 3)


class TestResolvePortalLink:
    def test_linking_two_unlinked_tiles_generates_a_shared_pair_id(self, test_db):
        _tile(test_db, 0, 0)
        _tile(test_db, 5, 5)
        p1, p2 = _patch_mongo(test_db)
        with p1, p2:
            result = hmr._resolve_portal_link(0, 0, {"link_to": {"q": 5, "r": 5}})
            partner = test_db["hex_map_tiles"].find_one({"q": 5, "r": 5})

        assert result["pair_id"]
        assert partner["portal"]["pair_id"] == result["pair_id"]

    def test_linking_steals_a_tile_away_from_its_old_partner(self, test_db):
        """A -- B already paired. Link A -- C instead: B must end up
        unlinked (not left dangling with a pair_id nobody else shares)."""
        _tile(test_db, 0, 0, pair_id="old-pair")   # A
        _tile(test_db, 5, 5, pair_id="old-pair")   # B
        _tile(test_db, 9, 9)                        # C, unlinked
        p1, p2 = _patch_mongo(test_db)
        with p1, p2:
            result = hmr._resolve_portal_link(0, 0, {"link_to": {"q": 9, "r": 9}})
            tile_a = test_db["hex_map_tiles"].find_one({"q": 0, "r": 0})
            tile_b = test_db["hex_map_tiles"].find_one({"q": 5, "r": 5})
            tile_c = test_db["hex_map_tiles"].find_one({"q": 9, "r": 9})

        new_pair_id = result["pair_id"]
        assert new_pair_id != "old-pair"
        assert tile_c["portal"]["pair_id"] == new_pair_id
        # tile_a itself isn't re-written by _resolve_portal_link (the
        # caller applies `result` as tile_a's own update) — only the
        # SIDE EFFECTS (clearing B) are asserted here.
        assert tile_b["portal"] is None

    def test_relinking_both_sides_away_clears_both_old_partners(self, test_db):
        """A--B and C--D both paired. Link A--C: B and D both end up
        unlinked, A and C share a brand new pair_id."""
        _tile(test_db, 0, 0, pair_id="pair-ab")   # A
        _tile(test_db, 1, 1, pair_id="pair-ab")   # B
        _tile(test_db, 2, 2, pair_id="pair-cd")   # C
        _tile(test_db, 3, 3, pair_id="pair-cd")   # D
        p1, p2 = _patch_mongo(test_db)
        with p1, p2:
            result = hmr._resolve_portal_link(0, 0, {"link_to": {"q": 2, "r": 2}})
            tile_b = test_db["hex_map_tiles"].find_one({"q": 1, "r": 1})
            tile_c = test_db["hex_map_tiles"].find_one({"q": 2, "r": 2})
            tile_d = test_db["hex_map_tiles"].find_one({"q": 3, "r": 3})

        assert tile_b["portal"] is None
        assert tile_d["portal"] is None
        assert tile_c["portal"]["pair_id"] == result["pair_id"]

    def test_unlinking_clears_both_this_tile_and_its_partner(self, test_db):
        _tile(test_db, 0, 0, pair_id="pair-ab")
        _tile(test_db, 1, 1, pair_id="pair-ab")
        p1, p2 = _patch_mongo(test_db)
        with p1, p2:
            result = hmr._resolve_portal_link(0, 0, None)
            tile_b = test_db["hex_map_tiles"].find_one({"q": 1, "r": 1})

        assert result is None
        assert tile_b["portal"] is None

    def test_linking_a_tile_to_itself_is_a_no_op(self, test_db):
        _tile(test_db, 0, 0, pair_id="pair-ab")
        _tile(test_db, 1, 1, pair_id="pair-ab")
        p1, p2 = _patch_mongo(test_db)
        with p1, p2:
            result = hmr._resolve_portal_link(0, 0, {"link_to": {"q": 0, "r": 0}})
            tile_b = test_db["hex_map_tiles"].find_one({"q": 1, "r": 1})

        assert result == {"pair_id": "pair-ab"}
        assert tile_b["portal"]["pair_id"] == "pair-ab"
