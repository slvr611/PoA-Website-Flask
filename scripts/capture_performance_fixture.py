"""
One-time capture of a realistic-volume, realistic-shape data snapshot from
the live database, used to seed mongomock for tests/test_performance_regression.py.

This exists because the performance bugs found and fixed this session
(characters list, new-character form, temperament overview — all
unprojected full-collection fetches) only show up as "slow" when the
documents involved carry their REAL field complexity (calculated
breakdowns, modifiers, job_details, etc. — nation documents run 40-50KB+
each). Hand-crafted fake documents with a handful of fields wouldn't
reproduce that; real documents, trimmed to a representative sample size,
do.

This is a read-only capture (no writes to production). Run once from the
project root to (re)generate the fixture:

    python scripts/capture_performance_fixture.py

Output: tests/fixtures/performance_snapshot.json
"""
import json
import os

from bson import json_util

from app_core import mongo

OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests", "fixtures", "performance_snapshot.json",
)

# Representative sample sizes — smaller than full production volume (206
# nations / 442 characters / 3281 pops / ~11500 hex tiles) to keep the
# checked-in fixture file a reasonable size, while still large enough that
# an unprojected-fetch regression shows up as a clear, real timing
# difference (not just noise) against a projected one.
NATION_SAMPLE_SIZE = 60
CHARACTER_SAMPLE_SIZE = 120
HEX_TILE_SAMPLE_SIZE = 3000

# Collections copied in full (small, or needed as complete lookup tables by
# calculate_all_fields — races/cultures/religions/regions are referenced by
# id from sampled nations/characters, so need to be complete, not sampled).
FULL_COLLECTIONS = [
    "races", "cultures", "religions", "regions", "diplo_relations",
    "trade_routes", "markets", "market_links", "diseases", "global_modifiers",
    "wonders", "district_categories", "district_defs", "mrp_defs", "players",
    "titles",
]


def capture():
    snapshot = {}

    nations = list(mongo.db.nations.find({}).limit(NATION_SAMPLE_SIZE))
    snapshot["nations"] = nations
    print(f"nations: captured {len(nations)} of {mongo.db.nations.count_documents({})}")

    # Pops belong to the SAMPLED nations specifically (their full, real pop
    # sets) rather than an independent random sample — the nation page's
    # pagination and calculate_all_fields both key off "this nation's own
    # pops", so a test nation needs its real, complete pop set to be a
    # faithful stand-in for a real page load.
    nation_ids = [str(n["_id"]) for n in nations]
    pops = list(mongo.db.pops.find({"nation": {"$in": nation_ids}}))
    snapshot["pops"] = pops
    print(f"pops: captured {len(pops)} (all pops of the {len(nations)} sampled nations)")

    characters = list(mongo.db.characters.find({}).limit(CHARACTER_SAMPLE_SIZE))
    snapshot["characters"] = characters
    print(f"characters: captured {len(characters)} of {mongo.db.characters.count_documents({})}")

    hex_map_tiles = list(mongo.db.hex_map_tiles.find({}).limit(HEX_TILE_SAMPLE_SIZE))
    snapshot["hex_map_tiles"] = hex_map_tiles
    print(f"hex_map_tiles: captured {len(hex_map_tiles)} of {mongo.db.hex_map_tiles.count_documents({})}")

    for name in FULL_COLLECTIONS:
        docs = list(mongo.db[name].find({}))
        snapshot[name] = docs
        print(f"{name}: captured {len(docs)} (full)")

    # Only Pending changes — the nation page route queries Pending changes
    # for the one nation being viewed, but the full collection can run to
    # many thousands of historical (Approved/Denied) records, which would
    # bloat this fixture for no benefit to what's actually being tested.
    pending_changes = list(mongo.db.changes.find({"status": "Pending"}))
    snapshot["changes"] = pending_changes
    print(f"changes: captured {len(pending_changes)} (Pending only, of {mongo.db.changes.count_documents({})} total)")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(json_util.dumps(snapshot))

    size_mb = os.path.getsize(OUTPUT_PATH) / 1024 / 1024
    print(f"\nWrote {OUTPUT_PATH} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    capture()
