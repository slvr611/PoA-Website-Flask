"""
One-off repair: forces a fresh rebuild_tile_chunks() run to resync
hex_map_tile_chunks from the authoritative hex_map_tiles collection.

Needed after the 2026-10-13 "tiles disappeared" report — rebuild_tile_chunks()
had a TOCTOU bug (fixed in this same change) where a tile write landing
during its several-second full-table scan could get silently clobbered by
the scan's own stale result, while chunk_synced_version was unconditionally
stamped as if the rebuild were fully caught up — so the mismatch could never
self-heal. This script forces one clean rebuild (now race-safe) to repair
whatever drift already accumulated in production before the fix.

Safe to re-run — it's exactly what get_all_tiles_from_chunks's self-heal path
already does automatically on a version mismatch; this just forces it once,
immediately, regardless of whether a mismatch is currently detected (so it
also catches drift the version counter itself wouldn't otherwise notice: a
corrupted mirror with chunk_synced_version == tile_version by coincidence,
which is exactly what happened here).

Also bumps tile_version at the end (beyond whatever rebuild_tile_chunks
itself settles on), unconditionally. A normal self-heal only runs because
some write already bumped tile_version, so clients' cached ETags (/api/
hex-map/tiles is ETag'd on tile_version — see routes/hex_map_routes.py)
are already invalidated by the time it happens. This script's repair isn't
triggered by a write — rebuild_tile_chunks alone can leave tile_version
completely unchanged (confirmed live: it stayed at 6779 before and after),
so without this, every browser's cached map tiles response keeps matching
the old ETag and a plain refresh just gets served a 304 + its stale,
pre-repair cached copy, even though the server-side data is already fixed
(see the 2026-10-13 "still shows the gaps after refresh" follow-up report).

Run from the project root: python -m scripts.repair_tile_chunk_mirror
"""
from app_core import mongo
from helpers.hex_map_helpers import rebuild_tile_chunks, bump_tile_version


def main():
    cfg = mongo.db.global_modifiers.find_one({"name": "hex_map_config"}) or {}
    print("Before repair:", {k: cfg.get(k) for k in ("tile_version", "chunk_synced_version")})

    raw_before = mongo.db.hex_map_tiles.count_documents({})
    mirror_before = sum(len(c.get("tiles", [])) for c in mongo.db.hex_map_tile_chunks.find({}, {"tiles": 1}))
    print(f"Before repair: raw={raw_before} mirror={mirror_before}")

    version = rebuild_tile_chunks()
    print("rebuild_tile_chunks() stamped chunk_synced_version:", version)

    cfg_after = mongo.db.global_modifiers.find_one({"name": "hex_map_config"}) or {}
    print("After repair:", {k: cfg_after.get(k) for k in ("tile_version", "chunk_synced_version")})

    raw_after = mongo.db.hex_map_tiles.count_documents({})
    mirror_after = sum(len(c.get("tiles", [])) for c in mongo.db.hex_map_tile_chunks.find({}, {"tiles": 1}))
    print(f"After repair: raw={raw_after} mirror={mirror_after}")

    tile = None
    for chunk in mongo.db.hex_map_tile_chunks.find({}):
        for t in chunk.get("tiles", []):
            if t.get("q") == 106 and t.get("r") == -32:
                tile = t
                break
        if tile:
            break
    print("Tile (106,-32) in mirror now:", tile)

    bumped = bump_tile_version()
    print("Bumped tile_version to invalidate client-side ETag caches:", bumped)


if __name__ == "__main__":
    main()
