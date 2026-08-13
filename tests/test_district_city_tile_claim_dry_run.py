"""
Regression tests for helpers.ai_decision_helpers._claim_district_tile /
_claim_city_tile's dry_run gating, and for the deferred-write (pending_tiles)
path those functions now go through during a real tick run.

Production incident #1 (dry_run): before dry_run existed, every
/ai_goals_preview page load called straight through to these functions and
wrote a REAL district/city claim onto the hex map tile — without ever saving
the corresponding entry into the owning nation's `districts`/`cities` array
(the preview's computed nation changes were only ever rendered, never
persisted). Repeated preview views for the same nation kept adding orphaned
claims: hex_map_tiles ended up with more districts/cities than the nation
actually had, silently, because production/job calculations read from
nation.districts/cities, never from the map tile itself.
migrations/fix_orphaned_district_tile_claims.py cleans up that damage; these
tests pin the dry_run fix itself so the bug class can't silently regress.

Gap #2 (pending_tiles): even with dry_run fixed, a REAL (non-preview) tick
call still wrote to hex_map_tiles immediately, mid-compute-phase — the one
write in the whole tick that didn't respect "nothing touches the database
until the single commit phase at the end" (see tick_helpers.tick()'s
matching comment, and _queue_tile_write's docstring). A tick that failed
after claiming tiles but before its commit phase left those claims
permanently on the map with no nation.districts/cities entry — the same
class of damage as incident #1, just from a different trigger. These tests
pin the fix: a real (dry_run=False) claim with a pending_tiles list queues
instead of writing, and is only applied once that queue is committed.
"""
from unittest.mock import MagicMock, patch

import helpers.ai_decision_helpers as adh
import helpers.tick_helpers as th


def _insert_tile(test_db, q=5, r=-3, node=None):
    test_db["hex_map_tiles"].insert_one({
        "q": q, "r": r, "terrain": "forest", "owner": "Test Nation",
        "node": node,
    })
    return q, r


def _patched_mongo(test_db):
    """Both ai_decision_helpers and tick_helpers hold their own `mongo` name
    bound at import time (each does `from app_core import mongo`) — patch
    both so a claim's immediate-write fallback (which goes through
    tick_helpers._queue_tile_write) lands in the same test_db the test reads
    back from."""
    mock = MagicMock(db=test_db)
    return patch.object(adh, "mongo", mock), patch.object(th, "mongo", mock)


class TestClaimDistrictTileDryRun:
    def test_dry_run_true_writes_nothing(self, test_db):
        coord = _insert_tile(test_db)
        p1, p2 = _patched_mongo(test_db)
        with p1, p2:
            adh._claim_district_tile("Test Nation", "abc12345", "farm", "Farm", coord, dry_run=True)
        tile = test_db["hex_map_tiles"].find_one({"q": coord[0], "r": coord[1]})
        assert tile.get("district") in (None,), "dry_run=True must never write a district claim"

    def test_dry_run_true_repeated_calls_still_write_nothing(self, test_db):
        """Directly reproduces the historical bug scenario: the same preview
        page (same nation/coord) is viewed multiple times in a row — none of
        those views may ever leave a claim behind."""
        coord = _insert_tile(test_db)
        p1, p2 = _patched_mongo(test_db)
        with p1, p2:
            for _ in range(5):
                adh._claim_district_tile("Test Nation", "abc12345", "farm", "Farm", coord, dry_run=True)
        tile = test_db["hex_map_tiles"].find_one({"q": coord[0], "r": coord[1]})
        assert tile.get("district") in (None,)

    def test_dry_run_false_no_pending_list_writes_immediately(self, test_db):
        """A caller invoking the claim function directly, outside tick()
        (no pending_tiles list) — e.g. a script or test — must keep working
        exactly like before: an immediate write."""
        coord = _insert_tile(test_db)
        p1, p2 = _patched_mongo(test_db)
        with p1, p2:
            adh._claim_district_tile("Test Nation", "abc12345", "farm", "Farm", coord, dry_run=False)
        tile = test_db["hex_map_tiles"].find_one({"q": coord[0], "r": coord[1]})
        assert tile["district"] == {"id": "abc12345", "def_key": "farm", "display_name": "Farm", "type": ""}

    def test_dry_run_true_still_returns_node_key_for_scoring(self, test_db):
        """dry_run must only suppress the write — the node-key read used by
        the AI's district scoring still has to work during a preview."""
        coord = _insert_tile(test_db, node={"resource_type": "iron"})
        p1, p2 = _patched_mongo(test_db)
        with p1, p2:
            node_key = adh._claim_district_tile("Test Nation", "abc12345", "farm", "Farm", coord, dry_run=True)
        assert node_key == "iron"

    def test_dry_run_false_with_pending_list_defers_the_write(self, test_db):
        """The tick-driven path: a real (non-preview) claim with a
        pending_tiles list must NOT touch the database yet — it only queues.
        This is what closes the "compute phase does a real write" gap."""
        coord = _insert_tile(test_db)
        pending_tiles = []
        p1, p2 = _patched_mongo(test_db)
        with p1, p2:
            adh._claim_district_tile(
                "Test Nation", "abc12345", "farm", "Farm", coord,
                dry_run=False, pending_tiles=pending_tiles,
            )
        tile = test_db["hex_map_tiles"].find_one({"q": coord[0], "r": coord[1]})
        assert tile.get("district") in (None,), "must not write until the queue is committed"
        assert len(pending_tiles) == 1
        assert pending_tiles[0]["set"]["district"] == {
            "id": "abc12345", "def_key": "farm", "display_name": "Farm", "type": "",
        }

    def test_committing_the_queue_applies_the_claim(self, test_db):
        """End-to-end: queue a claim, commit the queue, confirm the write
        lands — proving the deferral is real deferral, not a dropped write."""
        coord = _insert_tile(test_db)
        pending_tiles = []
        p1, p2 = _patched_mongo(test_db)
        with p1, p2:
            adh._claim_district_tile(
                "Test Nation", "abc12345", "farm", "Farm", coord,
                dry_run=False, pending_tiles=pending_tiles,
            )
            th._commit_pending_tile_writes(pending_tiles)
        tile = test_db["hex_map_tiles"].find_one({"q": coord[0], "r": coord[1]})
        assert tile["district"] == {"id": "abc12345", "def_key": "farm", "display_name": "Farm", "type": ""}


class TestClaimCityTileDryRun:
    def test_dry_run_true_writes_nothing(self, test_db):
        coord = _insert_tile(test_db)
        p1, p2 = _patched_mongo(test_db)
        with p1, p2:
            adh._claim_city_tile("Test Nation", "city1234", "capital", coord, set_capital=True, dry_run=True)
        tile = test_db["hex_map_tiles"].find_one({"q": coord[0], "r": coord[1]})
        assert tile.get("city") in (None,), "dry_run=True must never write a city claim"
        assert not tile.get("capital"), "dry_run=True must never set capital either"

    def test_dry_run_false_no_pending_list_writes_immediately(self, test_db):
        coord = _insert_tile(test_db)
        p1, p2 = _patched_mongo(test_db)
        with p1, p2:
            adh._claim_city_tile("Test Nation", "city1234", "capital", coord, set_capital=True, dry_run=False)
        tile = test_db["hex_map_tiles"].find_one({"q": coord[0], "r": coord[1]})
        assert tile["city"] == {"id": "city1234", "name": "", "type": "capital"}
        assert tile["capital"] is True

    def test_dry_run_false_with_pending_list_defers_the_write(self, test_db):
        coord = _insert_tile(test_db)
        pending_tiles = []
        p1, p2 = _patched_mongo(test_db)
        with p1, p2:
            adh._claim_city_tile(
                "Test Nation", "city1234", "capital", coord,
                set_capital=True, dry_run=False, pending_tiles=pending_tiles,
            )
        tile = test_db["hex_map_tiles"].find_one({"q": coord[0], "r": coord[1]})
        assert tile.get("city") in (None,)
        assert not tile.get("capital")
        assert pending_tiles[0]["set"] == {
            "city": {"id": "city1234", "name": "", "type": "capital"},
            "capital": True,
        }


class TestCommitPendingTileWrites:
    """Unit-level coverage of the commit helper itself, independent of the
    claim functions above."""

    def test_empty_queue_is_a_no_op(self, test_db):
        with patch.object(th, "mongo", MagicMock(db=test_db)):
            th._commit_pending_tile_writes([])  # must not raise

    def test_multiple_writes_to_different_tiles_all_apply(self, test_db):
        q1, r1 = _insert_tile(test_db, q=1, r=1)
        q2, r2 = _insert_tile(test_db, q=2, r=2)
        t1 = test_db["hex_map_tiles"].find_one({"q": q1, "r": r1})
        t2 = test_db["hex_map_tiles"].find_one({"q": q2, "r": r2})
        pending_tiles = [
            {"_id": t1["_id"], "set": {"district": {"id": "a", "def_key": "farm", "display_name": "Farm", "type": ""}}},
            {"_id": t2["_id"], "set": {"district": {"id": "b", "def_key": "quarry", "display_name": "Quarry", "type": ""}}},
        ]
        with patch.object(th, "mongo", MagicMock(db=test_db)):
            th._commit_pending_tile_writes(pending_tiles)
        assert test_db["hex_map_tiles"].find_one({"_id": t1["_id"]})["district"]["def_key"] == "farm"
        assert test_db["hex_map_tiles"].find_one({"_id": t2["_id"]})["district"]["def_key"] == "quarry"

    def test_two_writes_to_the_same_tile_are_merged_last_wins_per_field(self, test_db):
        """A district claim and a later, unrelated field write to the same
        tile within one tick must both survive — merged into one update,
        not one clobbering the other."""
        q, r = _insert_tile(test_db)
        t = test_db["hex_map_tiles"].find_one({"q": q, "r": r})
        pending_tiles = [
            {"_id": t["_id"], "set": {"district": {"id": "a", "def_key": "farm", "display_name": "Farm", "type": ""}}},
            {"_id": t["_id"], "set": {"node": {"resource_type": "iron"}}},
        ]
        with patch.object(th, "mongo", MagicMock(db=test_db)):
            th._commit_pending_tile_writes(pending_tiles)
        final = test_db["hex_map_tiles"].find_one({"_id": t["_id"]})
        assert final["district"]["def_key"] == "farm"
        assert final["node"] == {"resource_type": "iron"}
