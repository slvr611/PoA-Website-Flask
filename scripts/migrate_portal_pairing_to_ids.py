"""One-time migration: replace color-based portal pairing with explicit
pair_id links.

Context: portals used to pair up by sharing a `color` string picked from a
small fixed palette — _build_portal_map only formed a pair when EXACTLY 2
tiles shared a color, so a 3rd or 4th tile accidentally reusing the same
color (which was allowed, since colors were meant to be reusable for
visual variety) silently broke pairing for every tile sharing it. See the
2026-10-11 "Taika/Bloodlight Vigil" report, where this happened for real.

This script:
  1. Groups existing portal tiles by their current `color`.
  2. For every color with EXACTLY 2 tiles (today's working pairs), assigns
     them a freshly generated, globally unique `pair_id` and drops `color`.
  3. For every color with != 2 tiles (the broken groups), drops `color` and
     leaves the tiles as unlinked portals (`portal: {}` — still flagged as
     portal tiles, just with no pair_id) rather than guessing which 2 of N
     were meant to go together. These need a human to re-link them via the
     map editor's new "Link to" picker.

Safe to re-run: tiles that already have a pair_id (no `color` left) are
left untouched.

Usage:
    python scripts/migrate_portal_pairing_to_ids.py           # dry run
    python scripts/migrate_portal_pairing_to_ids.py --apply   # writes
"""
import argparse
import os
import sys
import uuid

# Known-correct resolutions for color groups that collided (more than 2
# tiles sharing one color) and can't be auto-resolved generically — filled
# in from the 2026-10-11 Taika/Bloodlight Vigil investigation, which
# traced exactly which 2 of the 4 "green" tiles were meant to go together:
# Taika's (83, 11) with Bloodlight Vigil's (96, 11), and the unrelated
# far-away (29, 4)/(33, -5) pair that happened to collide onto the same
# color. Each entry is a frozenset of exactly 2 (q, r) tuples.
MANUAL_PAIR_OVERRIDES = [
    frozenset({(83, 11), (96, 11)}),
    frozenset({(29, 4), (33, -5)}),
]

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from pymongo import MongoClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually write changes (default: dry run)")
    args = parser.parse_args()

    uri = os.getenv("MONGO_URI")
    if not uri:
        print("ERROR: MONGO_URI not set")
        sys.exit(1)
    client = MongoClient(uri)
    db_name = uri.split("/")[-1].split("?")[0]
    db = client[db_name]

    tiles = list(db.hex_map_tiles.find(
        {"portal": {"$exists": True, "$ne": None}},
        {"q": 1, "r": 1, "owner": 1, "portal": 1},
    ))
    print(f"Found {len(tiles)} portal tiles.")

    by_color = {}
    already_migrated = []
    for t in tiles:
        portal = t.get("portal") or {}
        if portal.get("pair_id"):
            already_migrated.append(t)
            continue
        color = portal.get("color", "")
        if color:
            by_color.setdefault(color, []).append(t)

    if already_migrated:
        print(f"{len(already_migrated)} tile(s) already have a pair_id — left untouched.")

    for color, color_tiles in sorted(by_color.items()):
        positions = [(t["q"], t["r"]) for t in color_tiles]
        if len(color_tiles) == 2:
            pair_id = uuid.uuid4().hex[:8]
            print(f"  [PAIR]    color={color!r} {positions} -> pair_id={pair_id}")
            if args.apply:
                for t in color_tiles:
                    db.hex_map_tiles.update_one(
                        {"_id": t["_id"]}, {"$set": {"portal": {"pair_id": pair_id}}},
                    )
            continue

        by_pos = {(t["q"], t["r"]): t for t in color_tiles}
        resolved_positions = set()
        applied_overrides = []
        for override in MANUAL_PAIR_OVERRIDES:
            if override <= by_pos.keys() and not (override & resolved_positions):
                applied_overrides.append(override)
                resolved_positions |= override

        if applied_overrides:
            for override in applied_overrides:
                pair_id = uuid.uuid4().hex[:8]
                print(f"  [MANUAL]  color={color!r} {sorted(override)} -> pair_id={pair_id} (known resolution)")
                if args.apply:
                    for pos in override:
                        db.hex_map_tiles.update_one(
                            {"_id": by_pos[pos]["_id"]}, {"$set": {"portal": {"pair_id": pair_id}}},
                        )

        remaining = [t for t in color_tiles if (t["q"], t["r"]) not in resolved_positions]
        if remaining:
            remaining_positions = [(t["q"], t["r"]) for t in remaining]
            print(f"  [ORPHAN]  color={color!r} {remaining_positions} ({len(remaining)} tiles, no known resolution — "
                  f"needs manual re-linking in the map editor)")
            if args.apply:
                for t in remaining:
                    db.hex_map_tiles.update_one({"_id": t["_id"]}, {"$set": {"portal": {}}})

    if not args.apply:
        print("\nDry run only — pass --apply to write these changes.")


if __name__ == "__main__":
    main()
