"""
Fix orphaned AI district tile claims.

A tile claim is "orphaned" when tile.district.id does not match any _id in the
owning nation's districts array.  These were created by the AI Goals Preview
tool before dry_run=True was added — each preview page load wrote a tile claim
without saving the nation document.

This script:
  1. Scans all hex_map_tiles with a district claim.
  2. For each tile, looks up the owning nation and checks whether the district id
     exists in that nation's districts array.
  3. If not found: clears tile.district (unsets the claim).
  4. Reports what it fixed.

Run once after deploying the dry_run fix:
    python -m migrations.fix_orphaned_district_tile_claims
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_core import app, mongo


def run(dry_run=False):
    with app.app_context():
        # Build {nation_name -> set(_id)} index for fast lookup
        nation_district_ids = {}
        for nation in mongo.db.nations.find({}, {"name": 1, "districts": 1}):
            name = nation.get("name", "")
            ids = set()
            for d in nation.get("districts", []):
                if isinstance(d, dict) and d.get("_id"):
                    ids.add(d["_id"])
            nation_district_ids[name] = ids

        tiles_with_district = list(mongo.db.hex_map_tiles.find(
            {"district": {"$exists": True, "$ne": None}},
            {"q": 1, "r": 1, "owner": 1, "district": 1}
        ))

        orphaned = []
        for tile in tiles_with_district:
            claim = tile.get("district")
            if not isinstance(claim, dict):
                continue
            claim_id = claim.get("id", "")
            owner = tile.get("owner", "")
            if not claim_id or not owner:
                continue
            # Skip special non-AI district formats (imperial quarters, etc.)
            if claim.get("imperial"):
                continue
            # Only process AI-generated IDs (8-char lowercase hex)
            if len(claim_id) != 8 or not all(c in "0123456789abcdef" for c in claim_id):
                continue
            nation_ids = nation_district_ids.get(owner, set())
            if claim_id not in nation_ids:
                orphaned.append(tile)

        print(f"Found {len(orphaned)} orphaned tile claims "
              f"({'DRY RUN — no changes' if dry_run else 'will clear'}):")
        for tile in orphaned:
            claim = tile.get("district", {})
            print(f"  ({tile['q']},{tile['r']}) owner={tile.get('owner')} "
                  f"def_key={claim.get('def_key')} id={claim.get('id')}")

        if not dry_run and orphaned:
            ids_to_clear = [t["_id"] for t in orphaned]
            result = mongo.db.hex_map_tiles.update_many(
                {"_id": {"$in": ids_to_clear}},
                {"$unset": {"district": ""}}
            )
            print(f"\nCleared {result.modified_count} orphaned tile claims.")
        elif dry_run:
            print("\n(dry_run=True — re-run with dry_run=False to apply)")


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    if dry:
        print("DRY RUN mode. Pass --apply to actually clear orphaned claims.\n")
    run(dry_run=dry)
