"""
Reconcile the hex map with AI nations' districts/cities.

Two one-way, non-destructive operations per nation (via
helpers.ai_decision_helpers.sync_nation_districts / sync_nation_cities):

  1. Map -> Nation: a district/city placed on a tile the nation owns, but
     missing from that nation's own districts/cities array, gets appended
     to the nation.
  2. Nation -> Map: a district/city in the nation's own array (a real one —
     blank placeholder slots and legacy type-based districts, which never
     have a map presence by design, are skipped) that isn't placed on any
     owned tile gets placed on the best legal tile available, using the
     same placement logic the AI itself uses when building new ones.

Nomadic nations are skipped for direction 2 (their districts/cities live on
the nation doc only, by design) but still get direction 1 applied.

"AI nation" here means "not controlled by a real player" — the same
definition routes/admin_tool_routes.py's sync_cities admin tool already
uses: excludes any nation a real player's character rules, or that lists a
player directly in nation.players.

Dry run by default — prints what WOULD change without writing anything.
Pass --apply to perform the writes for real. Bumps the hex map's tile
version counter afterward so browsers refetch, same as the existing
sync_cities admin route does.

Run from the project root:
    python -m scripts.reconcile_map_and_ai_nations            (dry run)
    python -m scripts.reconcile_map_and_ai_nations --apply    (for real)
"""
import sys
from bson import ObjectId

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app_core import mongo
from helpers.ai_decision_helpers import sync_nation_districts, sync_nation_cities
from helpers.hex_map_helpers import bump_tile_version


def _player_nation_ids():
    """Nations controlled by a real player — same definition
    routes/admin_tool_routes.py's _get_player_nation_ids uses."""
    ids = set()
    for char in mongo.db.characters.find(
        {"player": {"$exists": True, "$ne": None, "$ne": ""},
         "ruling_nation_org": {"$exists": True, "$ne": None}},
        {"ruling_nation_org": 1, "_id": 0},
    ):
        rno = char.get("ruling_nation_org")
        if rno:
            try:
                ids.add(ObjectId(str(rno)))
            except Exception:
                pass
    for nation in mongo.db.nations.find(
        {"players": {"$exists": True, "$ne": [], "$ne": None}}, {"_id": 1}
    ):
        ids.add(nation["_id"])
    return ids


def _all_tiles_by_owner():
    """Batch-fetch every owned tile once, grouped by owner name — avoids an
    N+1 query per nation across ~190 nations."""
    by_owner = {}
    for t in mongo.db.hex_map_tiles.find(
        {"owner": {"$nin": [None, ""]}},
        {"q": 1, "r": 1, "terrain": 1, "district": 1, "city": 1, "wonder": 1,
         "capital": 1, "node": 1, "owner": 1},
    ):
        by_owner.setdefault(t.get("owner", ""), []).append(t)
    return by_owner


def main():
    apply = "--apply" in sys.argv

    player_ids = _player_nation_ids()
    ai_nations = list(mongo.db.nations.find({"_id": {"$nin": list(player_ids)}}).sort("name", 1))
    tiles_by_owner = _all_tiles_by_owner()

    print(f"{len(ai_nations)} AI nations. {'APPLYING' if apply else 'DRY RUN — nothing written'}\n")

    total = {
        "districts_added_to_nation": 0, "districts_placed_on_map": 0, "districts_unplaceable": 0,
        "cities_added_to_nation": 0, "cities_placed_on_map": 0, "cities_unplaceable": 0,
        "nations_touched": 0,
    }
    any_placed_on_map = False

    for n in ai_nations:
        name = n.get("name", "")
        owned = tiles_by_owner.get(name, [])
        tiles_with_district = [t for t in owned if t.get("district")]
        tiles_with_city = [t for t in owned if t.get("city")]

        d_report = sync_nation_districts(n, dry_run=not apply, tiles_with_district=tiles_with_district, owned_tiles=owned)
        c_report = sync_nation_cities(n, dry_run=not apply, tiles_with_city=tiles_with_city, owned_tiles=owned)

        touched = any(d_report[k] for k in ("added_to_nation", "placed_on_map", "unplaceable")) or \
            any(c_report[k] for k in ("added_to_nation", "placed_on_map", "unplaceable"))
        if not touched:
            continue

        total["nations_touched"] += 1
        print(f"=== {name} ===")
        if d_report["added_to_nation"]:
            total["districts_added_to_nation"] += len(d_report["added_to_nation"])
            for item in d_report["added_to_nation"]:
                print(f"  district ADD TO NATION: {item['def_key']} (id={item['id']}, node={item['node']})")
        if d_report["placed_on_map"]:
            total["districts_placed_on_map"] += len(d_report["placed_on_map"])
            any_placed_on_map = True
            for item in d_report["placed_on_map"]:
                print(f"  district PLACE ON MAP: {item['def_key']} (id={item['id']}) at {item['coord']} — {item['rationale']}")
        if d_report["unplaceable"]:
            total["districts_unplaceable"] += len(d_report["unplaceable"])
            for item in d_report["unplaceable"]:
                print(f"  district UNPLACEABLE: {item['def_key']} (id={item['id']}) — {item.get('reason', 'no legal tile')}")
        if d_report.get("skipped_nomadic"):
            for item in d_report["skipped_nomadic"]:
                print(f"  district skipped (nomadic nation): {item['def_key']} (id={item['id']})")

        if c_report["added_to_nation"]:
            total["cities_added_to_nation"] += len(c_report["added_to_nation"])
            for item in c_report["added_to_nation"]:
                print(f"  city ADD TO NATION: {item['type']} (id={item['id']})")
        if c_report["placed_on_map"]:
            total["cities_placed_on_map"] += len(c_report["placed_on_map"])
            any_placed_on_map = True
            for item in c_report["placed_on_map"]:
                print(f"  city PLACE ON MAP: {item['type']} (id={item['id']}) at {item['coord']} — {item['rationale']}")
        if c_report["unplaceable"]:
            total["cities_unplaceable"] += len(c_report["unplaceable"])
            for item in c_report["unplaceable"]:
                print(f"  city UNPLACEABLE: {item['type']} (id={item['id']})")
        if c_report.get("skipped_nomadic"):
            for item in c_report["skipped_nomadic"]:
                print(f"  city skipped (nomadic nation): {item['type']} (id={item['id']})")
        print()

    print("--- Summary ---")
    print(f"Nations with any mismatch: {total['nations_touched']}")
    print(f"Districts added to nation pages: {total['districts_added_to_nation']}")
    print(f"Districts placed on map: {total['districts_placed_on_map']}")
    print(f"Districts unplaceable (no legal tile / no matching district_defs): {total['districts_unplaceable']}")
    print(f"Cities added to nation pages: {total['cities_added_to_nation']}")
    print(f"Cities placed on map: {total['cities_placed_on_map']}")
    print(f"Cities unplaceable: {total['cities_unplaceable']}")

    if apply and any_placed_on_map:
        bump_tile_version()
        print("\nBumped hex map tile version so clients refetch.")
    elif not apply:
        print("\nRe-run with --apply to actually perform these changes.")


if __name__ == "__main__":
    main()
