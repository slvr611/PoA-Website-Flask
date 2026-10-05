"""
Tests for the hex_map_tile_chunks mirror (helpers/hex_map_helpers.py) — a
read-optimized copy of hex_map_tiles used only by "give me every tile"
consumers (_get_cached_all_tiles/get_all_tiles).

Background: a direct hex_map_tiles.find({}) scan of the full ~11,500-tile
map measured at ~8.6s live, dominated by per-document/round-trip overhead
rather than payload size (avgObjSize is only 114 bytes). Grouping tiles
into ~100 chunk documents cuts a full-map read to ~100 document fetches.
hex_map_tiles itself remains the single source of truth — every targeted
read/write elsewhere in the codebase is untouched; only the full-map-read
path changes.

Freshness model: a write that wants the mirror to stay warm calls
notify_tile_changed(tile_id) (or the _by_coord variant) right after
writing. Everything else only needs bump_tile_version() (already called by
every write path) — get_all_tiles_from_chunks self-heals via a full
rebuild whenever the chunk mirror's recorded chunk_synced_version doesn't
match the live tile_version, so a missed sync hook can only ever cost one
extra rebuild, never stale data.
"""
from unittest.mock import patch, MagicMock

import helpers.hex_map_helpers as hmh


def _patch_mongo(test_db):
    m = MagicMock()
    m.db = test_db
    return patch("helpers.hex_map_helpers.mongo", m)


def _insert_tile(db, q, r, **extra):
    doc = {"q": q, "r": r, **extra}
    result = db["hex_map_tiles"].insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


def _bump_version(db):
    db["global_modifiers"].update_one(
        {"name": "hex_map_config"}, {"$inc": {"tile_version": 1}}, upsert=True,
    )


class TestChunkId:
    def test_same_chunk_for_nearby_tiles(self):
        assert hmh._chunk_id(0, 0) == hmh._chunk_id(1, 1)

    def test_different_chunk_for_far_tiles(self):
        assert hmh._chunk_id(0, 0) != hmh._chunk_id(100, 100)

    def test_deterministic(self):
        assert hmh._chunk_id(5, -3) == hmh._chunk_id(5, -3)


class TestRebuildTileChunks:
    def test_groups_tiles_into_chunks_and_records_version(self, test_db):
        _insert_tile(test_db, 0, 0, terrain="plains", owner="Testland")
        _insert_tile(test_db, 100, 100, terrain="forest")
        _bump_version(test_db)

        with _patch_mongo(test_db):
            synced_version = hmh.rebuild_tile_chunks()

        chunks = list(test_db["hex_map_tile_chunks"].find({}))
        all_tiles = [t for c in chunks for t in c["tiles"]]
        assert len(all_tiles) == 2
        assert {(t["q"], t["r"]) for t in all_tiles} == {(0, 0), (100, 100)}
        # Two far-apart tiles land in different chunk documents.
        assert len(chunks) == 2

        config = test_db["global_modifiers"].find_one({"name": "hex_map_config"})
        assert config["chunk_synced_version"] == synced_version == 1

    def test_stale_chunks_are_dropped_on_rebuild(self, test_db):
        _insert_tile(test_db, 0, 0, terrain="plains")
        with _patch_mongo(test_db):
            hmh.rebuild_tile_chunks()

        # Tile removed (e.g. a coordinate cleanup) — rebuilding again must
        # drop the now-empty chunk rather than leaving a stale copy behind.
        test_db["hex_map_tiles"].delete_many({})
        with _patch_mongo(test_db):
            hmh.rebuild_tile_chunks()

        assert list(test_db["hex_map_tile_chunks"].find({})) == []

    def test_excludes_id_field(self, test_db):
        _insert_tile(test_db, 0, 0, terrain="plains")
        with _patch_mongo(test_db):
            hmh.rebuild_tile_chunks()

        chunk = test_db["hex_map_tile_chunks"].find_one({})
        assert "_id" not in chunk["tiles"][0]


class TestSyncTileChunk:
    def test_updates_existing_tile_in_its_chunk(self, test_db):
        tile = _insert_tile(test_db, 0, 0, terrain="plains", owner="Testland")
        with _patch_mongo(test_db):
            hmh.rebuild_tile_chunks()
            test_db["hex_map_tiles"].update_one({"_id": tile["_id"]}, {"$set": {"owner": "Newland"}})
            hmh._sync_tile_chunk(tile["_id"])

        chunk = test_db["hex_map_tile_chunks"].find_one({"_id": hmh._chunk_id(0, 0)})
        assert len(chunk["tiles"]) == 1
        assert chunk["tiles"][0]["owner"] == "Newland"

    def test_adds_new_tile_to_a_not_yet_existing_chunk(self, test_db):
        tile = _insert_tile(test_db, 50, 50, terrain="desert")
        with _patch_mongo(test_db):
            hmh._sync_tile_chunk(tile["_id"])

        chunk = test_db["hex_map_tile_chunks"].find_one({"_id": hmh._chunk_id(50, 50)})
        assert chunk is not None
        assert chunk["tiles"][0]["q"] == 50 and chunk["tiles"][0]["r"] == 50

    def test_missing_tile_is_a_noop(self, test_db):
        with _patch_mongo(test_db):
            hmh._sync_tile_chunk("nonexistent-id")
        assert list(test_db["hex_map_tile_chunks"].find({})) == []


class TestSyncTileChunkByCoord:
    def test_resolves_by_coordinate_not_id(self, test_db):
        _insert_tile(test_db, 7, 7, terrain="mountain", district={"id": "d1"})
        with _patch_mongo(test_db):
            hmh._sync_tile_chunk_by_coord(7, 7)

        chunk = test_db["hex_map_tile_chunks"].find_one({"_id": hmh._chunk_id(7, 7)})
        assert chunk["tiles"][0]["district"]["id"] == "d1"


class TestNotifyTileChanged:
    def test_bumps_version_and_syncs_chunk(self, test_db):
        tile = _insert_tile(test_db, 1, 1, terrain="plains")
        with _patch_mongo(test_db):
            hmh.notify_tile_changed(tile["_id"])

        config = test_db["global_modifiers"].find_one({"name": "hex_map_config"})
        assert config["tile_version"] == 1
        chunk = test_db["hex_map_tile_chunks"].find_one({"_id": hmh._chunk_id(1, 1)})
        assert chunk["tiles"][0]["q"] == 1

    def test_marks_chunk_synced_to_the_version_it_just_bumped(self, test_db):
        """Regression for the 2026-10-04 investigation: chunk_synced_version
        used to only ever advance inside rebuild_tile_chunks()'s full scan,
        so it permanently lagged tile_version by however many single-tile
        writes had happened since the last rebuild. That made the very next
        full-map read after ANY notify_tile_changed() call see a version
        mismatch and pay a full ~11,500-document rebuild regardless of the
        chunk mirror already being correct — defeating the entire point of
        syncing per-tile. Measured live as the dominant cost (~12s of ~22s)
        of approving a nation change, enough to time out Heroku requests."""
        tile = _insert_tile(test_db, 1, 1, terrain="plains")
        with _patch_mongo(test_db):
            hmh.notify_tile_changed(tile["_id"])

        config = test_db["global_modifiers"].find_one({"name": "hex_map_config"})
        assert config["tile_version"] == config["chunk_synced_version"] == 1

    def test_fast_path_taken_on_the_very_next_read_after_a_single_tile_write(self, test_db):
        """End-to-end: after notify_tile_changed, get_all_tiles_from_chunks
        must NOT call rebuild_tile_chunks() at all."""
        tile = _insert_tile(test_db, 1, 1, terrain="plains")
        with _patch_mongo(test_db):
            hmh.rebuild_tile_chunks()
            test_db["hex_map_tiles"].update_one({"_id": tile["_id"]}, {"$set": {"terrain": "forest"}})
            hmh.notify_tile_changed(tile["_id"])

            calls = []
            original = hmh.rebuild_tile_chunks
            hmh.rebuild_tile_chunks = lambda: (calls.append(1), original())[1]
            try:
                tiles = hmh.get_all_tiles_from_chunks()
            finally:
                hmh.rebuild_tile_chunks = original

        assert calls == []
        assert tiles[0]["terrain"] == "forest"

    def test_by_coord_variant_also_marks_chunk_synced(self, test_db):
        _insert_tile(test_db, 2, 2, terrain="plains")
        with _patch_mongo(test_db):
            hmh.notify_tile_changed_by_coord(2, 2)

        config = test_db["global_modifiers"].find_one({"name": "hex_map_config"})
        assert config["tile_version"] == config["chunk_synced_version"] == 1

    def test_concurrent_version_bump_leaves_chunk_synced_version_stale(self, test_db):
        """If some other write bumps tile_version in between this call's own
        bump and its mark-synced step, _mark_chunk_synced's filtered update
        must no-op rather than falsely claiming the mirror caught up to a
        version it never actually reached."""
        tile = _insert_tile(test_db, 1, 1, terrain="plains")
        with _patch_mongo(test_db):
            version = hmh.bump_tile_version()
            hmh._sync_tile_chunk(tile["_id"])
            _bump_version(test_db)  # a concurrent, unrelated bump
            hmh._mark_chunk_synced(version)  # stale by now — must not apply

        config = test_db["global_modifiers"].find_one({"name": "hex_map_config"})
        assert config["tile_version"] == 2
        assert config.get("chunk_synced_version") != 2


class TestProcessCachePatchedBySingleTileWrites:
    """_patch_process_tile_cache keeps the process-level full-tile-list
    cache warm across individual tile writes — without it, every single
    tile write (far more frequent in practice than full map reads) would
    force the next full-map read to pay the real aggregate-fetch cost all
    over again even with chunk_synced_version correctly tracked."""

    def test_warm_cache_reflects_an_update_without_rereading_mongo(self, test_db):
        tile = _insert_tile(test_db, 0, 0, terrain="plains", owner="Testland")
        with _patch_mongo(test_db):
            first = hmh.get_all_tiles_from_chunks()
            assert first[0]["owner"] == "Testland"

            test_db["hex_map_tiles"].update_one({"_id": tile["_id"]}, {"$set": {"owner": "Newland"}})
            hmh.notify_tile_changed(tile["_id"])

            # Corrupt the Mongo-side chunk mirror directly — if the cache
            # weren't patched and this fell back to a real read, it would
            # see this value instead of the true update.
            test_db["hex_map_tile_chunks"].update_many({}, {"$set": {"tiles.0.owner": "SHOULD_NOT_APPEAR"}})

            second = hmh.get_all_tiles_from_chunks()

        assert second[0]["owner"] == "Newland"

    def test_new_tile_is_appended_to_the_warm_cache(self, test_db):
        _insert_tile(test_db, 0, 0, terrain="plains")
        with _patch_mongo(test_db):
            first = hmh.get_all_tiles_from_chunks()
            assert len(first) == 1

            new_tile = _insert_tile(test_db, 5, 5, terrain="forest")
            hmh.notify_tile_changed(new_tile["_id"])

            test_db["hex_map_tile_chunks"].delete_many({})  # prove no refetch happens
            second = hmh.get_all_tiles_from_chunks()

        assert len(second) == 2
        assert {(t["q"], t["r"]) for t in second} == {(0, 0), (5, 5)}

    def test_does_not_patch_an_unpopulated_cache(self, test_db):
        """Nothing has read the full tile list yet this process — patching
        must be a no-op, not an error."""
        tile = _insert_tile(test_db, 0, 0, terrain="plains")
        with _patch_mongo(test_db):
            hmh.notify_tile_changed(tile["_id"])  # cache not built yet — must not raise
            tiles = hmh.get_all_tiles_from_chunks()

        assert len(tiles) == 1


class TestGetAllTilesFromChunks:
    def test_fast_path_used_when_version_matches(self, test_db):
        _insert_tile(test_db, 0, 0, terrain="plains")
        with _patch_mongo(test_db):
            hmh.rebuild_tile_chunks()

            # Directly corrupt the chunk mirror (simulating a stale-but-
            # version-matching mirror) to prove a matching version really
            # does take the fast path and skip a rebuild.
            test_db["hex_map_tile_chunks"].update_one(
                {}, {"$set": {"tiles.0.terrain": "CACHED_VALUE"}}
            )
            tiles = hmh.get_all_tiles_from_chunks()

        assert tiles[0]["terrain"] == "CACHED_VALUE"

    def test_self_heals_on_version_mismatch(self, test_db):
        _insert_tile(test_db, 0, 0, terrain="plains")
        with _patch_mongo(test_db):
            hmh.rebuild_tile_chunks()
            # A write that bumped tile_version without syncing the chunk
            # (simulating a missed hook) — the mirror is now stale.
            _bump_version(test_db)
            test_db["hex_map_tiles"].update_one({"q": 0, "r": 0}, {"$set": {"terrain": "REAL_VALUE"}})

            tiles = hmh.get_all_tiles_from_chunks()

        assert tiles[0]["terrain"] == "REAL_VALUE"

    def test_process_cache_reused_across_calls_on_same_connection(self, test_db):
        """The process-level cache (get_all_tiles_from_chunks's in-memory
        layer, separate from the Mongo-persisted chunk mirror) must skip
        even the chunk read entirely on a second call at the same version —
        this is what actually eliminates the per-request full-tile-read
        cost, since reading the chunk mirror itself still costs real time
        (measured live: no cheaper than a direct hex_map_tiles scan on this
        connection — the win is not re-reading it at all when nothing
        changed)."""
        _insert_tile(test_db, 0, 0, terrain="plains")
        with _patch_mongo(test_db):
            first = hmh.get_all_tiles_from_chunks()
            # Corrupt the chunk mirror directly — if the second call still
            # hits Mongo instead of the process cache, it would see this.
            test_db["hex_map_tile_chunks"].update_many({}, {"$set": {"tiles.0.terrain": "SHOULD_NOT_APPEAR"}})
            second = hmh.get_all_tiles_from_chunks()

        assert first == second
        assert second[0]["terrain"] == "plains"

    def test_process_cache_does_not_leak_across_different_connections(self, test_db):
        """Two different `mongo` objects (e.g. two tests, or — in
        production — any future reconnect) must never share the process
        cache just because both happen to be at the same tile_version
        number (a fresh database always starts at version 0)."""
        _insert_tile(test_db, 0, 0, terrain="plains", owner="ConnectionOne")
        with _patch_mongo(test_db):
            first = hmh.get_all_tiles_from_chunks()
        assert first[0]["owner"] == "ConnectionOne"

        import mongomock
        other_db = mongomock.MongoClient()["other_test"]
        _insert_tile(other_db, 0, 0, terrain="plains", owner="ConnectionTwo")
        with _patch_mongo(other_db):
            second = hmh.get_all_tiles_from_chunks()

        assert second[0]["owner"] == "ConnectionTwo"

    def test_builds_chunks_on_first_call_when_none_exist(self, test_db):
        _insert_tile(test_db, 0, 0, terrain="plains")
        with _patch_mongo(test_db):
            tiles = hmh.get_all_tiles_from_chunks()

        assert len(tiles) == 1
        assert tiles[0]["terrain"] == "plains"

    def test_matches_a_direct_full_scan(self, test_db):
        """Parity check: chunked output must be identical (as sets of
        (q, r, terrain, owner)) to a direct hex_map_tiles scan."""
        for i in range(20):
            _insert_tile(test_db, i, -i, terrain="plains", owner=f"Nation{i % 3}")

        with _patch_mongo(test_db):
            chunked = hmh.get_all_tiles_from_chunks()

        direct = list(test_db["hex_map_tiles"].find({}, {"_id": 0, "q": 1, "r": 1, "terrain": 1, "owner": 1}))

        def _key(t):
            return (t["q"], t["r"], t.get("terrain"), t.get("owner"))

        assert {_key(t) for t in chunked} == {_key(t) for t in direct}
        assert len(chunked) == len(direct) == 20


class TestGetAllTilesUsesChunkMirror:
    def test_get_all_tiles_returns_same_shape_as_before(self, test_db):
        _insert_tile(test_db, 0, 0, terrain="plains", owner="Testland", extra_field="x")
        with _patch_mongo(test_db):
            tiles = hmh.get_all_tiles()

        assert len(tiles) == 1
        t = tiles[0]
        assert t["q"] == 0 and t["r"] == 0
        assert t["terrain"] == "plains"
        assert t["owner"] == "Testland"
        assert "extra_field" not in t
        assert "_id" not in t
