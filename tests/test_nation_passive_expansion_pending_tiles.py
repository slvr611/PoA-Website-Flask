"""
Regression tests for a real production incident: nation_passive_expansion_tick
wrote hex_map_tiles ownership and nations.territory_types directly and
immediately, bypassing the pending/transactional commit system every other
tick write goes through (_queue_change/_commit_pending_changes, and
_queue_tile_write/_commit_pending_tile_writes for tile claims — see those
docstrings). tick()'s per-nation compute phase is documented to touch
nothing in the database until a single all-or-nothing commit at the end, so
a tick that failed anywhere after this function ran (a per-nation crash
later in the loop, or the final commit transaction itself aborting) still
left these tile claims and territory_types changes permanently on the map,
even though the tick's own summary reported "Tick FAILED and was fully
rolled back — no changes from this run were applied."

Concretely: on 2026-08-11, three tick attempts in a row failed at various
points (a NoneType crash mid-loop, a transient Mongo transaction abort at
commit, and a queued nations update failing to apply), and forensic diffing
of the S3 backups bracketing each attempt showed ~390 tiles across ~147
nations were passively claimed and kept despite every one of those ticks
reporting a full rollback.

The fix routes the tile writes through _queue_tile_write (the same
mechanism already used by district/city claims — see
test_district_city_tile_claim_dry_run.py) and drops the separate direct
nations.update_one for territory_types in favor of just setting
new_nation["territory_types"], which the existing per-nation "Tick Update
for X" _queue_change already picks up and commits transactionally.
"""
from unittest.mock import MagicMock, patch

import helpers.tick_helpers as th
import helpers.hex_map_helpers as hmh


def _insert_tiles(test_db):
    """One tile already owned by Test Nation, one unowned neighbor tile."""
    owned_id = test_db["hex_map_tiles"].insert_one(
        {"q": 0, "r": 0, "terrain": "plains", "owner": "Test Nation"}
    ).inserted_id
    unowned_id = test_db["hex_map_tiles"].insert_one(
        {"q": 1, "r": 0, "terrain": "forest", "owner": None}
    ).inserted_id
    return owned_id, unowned_id


def _make_nation():
    return {
        "_id": "nation-id-1",
        "name": "Test Nation",
        "passive_expansion_chance": 1.0,  # guarantee the roll succeeds
        "effective_territory": 100,
        "current_territory": 1,
        "resource_windfall_on_expansion": 0,
    }


class TestNationPassiveExpansionTickDefersTileWrites:
    def test_no_pending_tiles_writes_immediately(self, test_db):
        """A caller invoking this directly, outside tick() (no pending_tiles
        list) — e.g. a script or test — must keep working exactly like
        before: an immediate write."""
        owned_id, unowned_id = _insert_tiles(test_db)
        old_nation = _make_nation()
        new_nation = dict(old_nation)

        with patch.object(th, "mongo", MagicMock(db=test_db)), \
             patch.object(hmh, "select_passive_expansion_tiles", return_value=[(1, 0)]):
            th.nation_passive_expansion_tick(old_nation, new_nation, {})

        tile = test_db["hex_map_tiles"].find_one({"_id": unowned_id})
        assert tile["owner"] == "Test Nation"

    def test_with_pending_tiles_defers_the_write(self, test_db):
        """The tick-driven path: passing a pending_tiles list must NOT touch
        the database yet — only queue the claim. This is what closes the
        gap that let claims survive a tick that later failed and rolled
        back."""
        owned_id, unowned_id = _insert_tiles(test_db)
        old_nation = _make_nation()
        new_nation = dict(old_nation)
        pending_tiles = []

        with patch.object(th, "mongo", MagicMock(db=test_db)), \
             patch.object(hmh, "select_passive_expansion_tiles", return_value=[(1, 0)]):
            th.nation_passive_expansion_tick(old_nation, new_nation, {}, pending_tiles=pending_tiles)

        tile = test_db["hex_map_tiles"].find_one({"_id": unowned_id})
        assert tile["owner"] is None, "must not write the claim until the queue is committed"
        assert len(pending_tiles) == 1
        assert pending_tiles[0]["_id"] == unowned_id
        assert pending_tiles[0]["set"]["owner"] == "Test Nation"

    def test_a_later_failure_leaves_no_trace_when_deferred(self, test_db):
        """Simulates exactly the production incident: the claim is queued,
        then something else in the tick raises before the commit phase runs
        — the tile must be untouched, unlike the pre-fix behavior where the
        claim was already permanently written by this point."""
        owned_id, unowned_id = _insert_tiles(test_db)
        old_nation = _make_nation()
        new_nation = dict(old_nation)
        pending_tiles = []

        with patch.object(th, "mongo", MagicMock(db=test_db)), \
             patch.object(hmh, "select_passive_expansion_tiles", return_value=[(1, 0)]):
            th.nation_passive_expansion_tick(old_nation, new_nation, {}, pending_tiles=pending_tiles)
            # ... tick crashes here before reaching _commit_pending_tile_writes ...

        tile = test_db["hex_map_tiles"].find_one({"_id": unowned_id})
        assert tile["owner"] is None

    def test_committing_the_queue_applies_the_claim(self, test_db):
        """End-to-end: queue a claim, commit the queue, confirm the write
        lands — proving the deferral is real deferral, not a dropped
        write."""
        owned_id, unowned_id = _insert_tiles(test_db)
        old_nation = _make_nation()
        new_nation = dict(old_nation)
        pending_tiles = []

        with patch.object(th, "mongo", MagicMock(db=test_db)), \
             patch.object(hmh, "select_passive_expansion_tiles", return_value=[(1, 0)]):
            th.nation_passive_expansion_tick(old_nation, new_nation, {}, pending_tiles=pending_tiles)
            th._commit_pending_tile_writes(pending_tiles)

        tile = test_db["hex_map_tiles"].find_one({"_id": unowned_id})
        assert tile["owner"] == "Test Nation"

    def test_territory_types_reflects_the_claim_even_though_write_is_deferred(self, test_db):
        """territory_types used to come from a fresh aggregate query against
        hex_map_tiles — which, once the ownership write itself was deferred,
        would only ever see the pre-claim owners. It must instead be
        computed from the in-memory (already-updated) tile map."""
        owned_id, unowned_id = _insert_tiles(test_db)
        old_nation = _make_nation()
        new_nation = dict(old_nation)
        pending_tiles = []

        with patch.object(th, "mongo", MagicMock(db=test_db)), \
             patch.object(hmh, "select_passive_expansion_tiles", return_value=[(1, 0)]):
            th.nation_passive_expansion_tick(old_nation, new_nation, {}, pending_tiles=pending_tiles)

        assert new_nation["territory_types"] == {"plains": 1, "forest": 1}

    def test_registered_as_tile_pending_aware(self):
        """_dispatch only forwards pending_tiles to functions registered in
        this set — if nation_passive_expansion_tick isn't in it, the tick's
        own dispatch loop silently drops back to the old immediate-write
        behavior regardless of what the function itself supports."""
        assert th.nation_passive_expansion_tick in th._TILE_PENDING_AWARE_TICK_FUNCTIONS
