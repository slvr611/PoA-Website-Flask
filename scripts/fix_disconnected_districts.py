"""
Applies the full fix for the "Twinborn Elitria" corrupted-district report.
Companion to scripts/audit_disconnected_districts.py (read-only) — this one
writes. Five ordered phases, each seeing the settled state of the one before
it, since later phases can be affected by earlier ones (e.g. removing an
illegal Vandadorian Citadel can newly disconnect districts that were only
adjacent to it):

1. VANDADORIAN CITADEL REFUND — "vandadorian_citadel" requires
   requirements.name == "Vandador" (json-data/cities.json), so any other
   nation holding one has it through the same historical data corruption.
   Every such entry is refunded (json-data/cities.json cost) and removed
   from nation.cities, and every map tile referencing it is cleared.

2. CAPITAL FALLBACK — narrowly scoped to nations phase 1 leaves with no
   capital and no other city (confirmed: Pelopquartis's Vandadorian
   Citadel IS its capital, and it has no other city). Deliberately does
   NOT run the full fix_city_and_capital_placement pass: that tool also
   recenters any AI nation's capital onto its most central existing city
   world-wide, which is a separate, much broader maintenance job the
   admin tools already expose on demand — out of scope for a fix aimed at
   the disconnected-district/Vandadorian-Citadel report.

3. SYNC MISSING PLACEMENTS — sync_nation_districts/sync_nation_cities
   world-wide, apply=True, now guarded against creating new duplicates
   (see their world_city_ids/world_district_ids parameters). Places any
   nation.districts/cities entry that isn't on the map anywhere yet (e.g.
   Dyeak's "farm" district, id a1357680, was never on the map at all).

4. DUPLICATE RESOLUTION — the same city/district id sitting on 2 map tiles
   for one nation (the original bug: sync's "Nation -> Map" direction used
   to place a second copy when it couldn't see the real one). For each
   duplicate, keeps the tile whose def_key/type matches the nation's own
   record for that id (a mismatch means the OTHER copy is stale data from
   before the district/city was edited) and clears the other; ties (both
   copies match) are broken by keeping the one closer to the capital.

5. DISCONNECTED/ILLEGAL DISTRICT RELOCATION — checked fresh after phases
   1-4 may have removed a district's only anchor (e.g. Twinborn Elitria's
   forge/ranch, only adjacent to the ghost Vandadorian Citadel copy). Uses
   the same authoritative check as audit_disconnected_districts.py
   (_is_properly_placed — see there for why a hand-rolled adjacency check
   isn't enough: it must also exclude the district's own tile from acting
   as its own anchor, skip the special imperial-quarter claim, and folds in
   a second, related violation this same check catches for free — a
   district illegally sitting on a capital tile, which may only ever host
   a city). Relocated to a legal tile using the exact scoring the AI
   itself uses (_pick_district_tile) — refund-and-remove only if no legal
   tile exists anywhere, per "prefer moving over refunding".

Run from the project root:
    python -m scripts.fix_disconnected_districts            (dry run)
    python -m scripts.fix_disconnected_districts --apply    (writes for real)
"""
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app_core import mongo, json_data
from helpers.hex_map_helpers import hex_distance, bump_tile_version
from helpers.ai_decision_helpers import (
    _pick_district_tile, _base_prices, evaluate_nation_state, _weights_from_net,
    _pick_fallback_capital_tile, sync_nation_districts, sync_nation_cities,
)
from scripts.audit_disconnected_districts import _is_properly_placed


def _all_owned_tiles_by_nation():
    by_owner = {}
    for t in mongo.db.hex_map_tiles.find(
        {"owner": {"$nin": [None, ""]}},
        {"q": 1, "r": 1, "owner": 1, "city": 1, "district": 1, "capital": 1,
         "terrain": 1, "node": 1, "wonder": 1},
    ):
        by_owner.setdefault(t["owner"], []).append(t)
    return by_owner


def _find_duplicates(tiles, key):
    groups = defaultdict(list)
    for t in tiles:
        obj = t.get(key)
        if isinstance(obj, dict) and obj.get("id"):
            groups[obj["id"]].append(t)
    return {k: v for k, v in groups.items() if len(v) > 1}


def _label(key, obj):
    return obj.get("type", "generic") if key == "city" else obj.get("def_key", "")


def _refund_into(nation, cost):
    """Mutates nation["resource_storage"] in place and returns it — caller
    still needs to persist it. Mirrors fix_city_and_capital_placement's
    existing refund pattern exactly."""
    if not cost:
        return nation.get("resource_storage", {}) or {}
    storage = dict(nation.get("resource_storage", {}) or {})
    caps = nation.get("nation_resource_capacity") or {}
    for res, amt in cost.items():
        new_amount = storage.get(res, 0) + amt
        cap = caps.get(res)
        if cap is not None:
            new_amount = min(new_amount, cap)
        storage[res] = new_amount
    nation["resource_storage"] = storage
    return storage


# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------

def phase1_refund_illegal_vandadorian_citadels(dry_run):
    cost = json_data.get("cities", {}).get("vandadorian_citadel", {}).get("cost", {})
    actions = []
    lost_capital_nations = set()
    removed_tile_ids = set()
    for nation in list(mongo.db.nations.find({
        "cities.type": "vandadorian_citadel", "name": {"$ne": "Vandador"},
    })):
        citadels = [c for c in nation.get("cities", []) if c.get("type") == "vandadorian_citadel"]
        for c in citadels:
            cid = c["_id"]
            tiles = list(mongo.db.hex_map_tiles.find({"city.id": cid}))
            if any(t.get("capital") for t in tiles):
                lost_capital_nations.add(nation["name"])
            removed_tile_ids.update(t["_id"] for t in tiles)
            actions.append({
                "nation": nation["name"], "id": cid,
                "tiles": [(t["q"], t["r"]) for t in tiles], "refund": cost,
            })
            if not dry_run:
                storage = _refund_into(nation, cost)
                mongo.db.nations.update_one(
                    {"_id": nation["_id"]},
                    {"$pull": {"cities": {"_id": cid}}, "$set": {"resource_storage": storage}},
                )
                for t in tiles:
                    mongo.db.hex_map_tiles.update_one({"_id": t["_id"]}, {"$unset": {"city": "", "capital": ""}})
    return actions, lost_capital_nations, removed_tile_ids


# ---------------------------------------------------------------------------
# Phase 2 — narrow capital fallback, only for a nation phase 1 left with no
# capital AND no other city (see module docstring for why this doesn't just
# call the broader fix_city_and_capital_placement).
# ---------------------------------------------------------------------------

def phase2_assign_fallback_capital(lost_capital_nations, removed_tile_ids, dry_run):
    """removed_tile_ids: tile _ids phase 1 is removing the city/capital from
    — even in dry_run (where nothing was actually written), these must be
    treated as already cleared for the "does this nation still have another
    city/capital" check below (or the preview would wrongly see the
    about-to-be-removed capital as still present and report nothing
    needed). Critically, a cleared tile must stay IN the owned-tiles list
    (simulated as empty, not filtered out) since it's very often the only
    non-water empty land tile the nation has left and therefore the correct
    new capital candidate itself — filtering it out entirely previously
    left a nation with neither a capital nor any candidate to become one."""
    actions = []
    for name in sorted(lost_capital_nations):
        nation = mongo.db.nations.find_one({"name": name})
        if not nation:
            continue
        owned = []
        for t in mongo.db.hex_map_tiles.find({"owner": name}):
            if t["_id"] in removed_tile_ids:
                t = dict(t)
                t.pop("city", None)
                t.pop("capital", None)
            owned.append(t)
        has_other_city = any(
            isinstance(t.get("city"), dict) and t["city"].get("type") for t in owned
        )
        has_capital = any(t.get("capital") for t in owned)
        if has_other_city or has_capital:
            # Another city already covers this, or a capital already exists
            # elsewhere (e.g. this nation had 2 capital-flagged tiles) —
            # nothing to do.
            continue
        new_capital = _pick_fallback_capital_tile(owned)
        if not new_capital:
            actions.append({"nation": name, "new_capital": None})
            continue
        actions.append({"nation": name, "new_capital": (new_capital["q"], new_capital["r"])})
        if not dry_run:
            mongo.db.hex_map_tiles.update_one({"_id": new_capital["_id"]}, {"$set": {"capital": True}})
    return actions


# ---------------------------------------------------------------------------
# Phase 3 (sync) is just direct calls to the existing helpers — see main().
# ---------------------------------------------------------------------------

def phase3_sync_missing_placements(dry_run):
    player_ids = set()
    for char in mongo.db.characters.find(
        {"player": {"$exists": True, "$ne": None, "$ne": ""},
         "ruling_nation_org": {"$exists": True, "$ne": None}},
        {"ruling_nation_org": 1, "_id": 0},
    ):
        rno = char.get("ruling_nation_org")
        if rno:
            try:
                from bson import ObjectId
                player_ids.add(ObjectId(str(rno)))
            except Exception:
                pass
    for nation in mongo.db.nations.find(
        {"players": {"$exists": True, "$ne": [], "$ne": None}}, {"_id": 1}
    ):
        player_ids.add(nation["_id"])

    ai_nations = list(mongo.db.nations.find({"_id": {"$nin": list(player_ids)}}))
    tiles_by_owner = _all_owned_tiles_by_nation()
    world_city_ids, world_district_ids = set(), set()
    for tiles in tiles_by_owner.values():
        for t in tiles:
            c, d = t.get("city"), t.get("district")
            if isinstance(c, dict) and c.get("id"):
                world_city_ids.add(c["id"])
            if isinstance(d, dict) and d.get("id"):
                world_district_ids.add(d["id"])

    reports = []
    for n in ai_nations:
        owned = tiles_by_owner.get(n.get("name", ""), [])
        d_report = sync_nation_districts(
            n, dry_run=dry_run, tiles_with_district=owned, owned_tiles=owned,
            world_district_ids=world_district_ids,
        )
        c_report = sync_nation_cities(
            n, dry_run=dry_run, tiles_with_city=owned, owned_tiles=owned,
            world_city_ids=world_city_ids,
        )
        if any(d_report[k] for k in ("added_to_nation", "placed_on_map", "unplaceable", "skipped_duplicate_elsewhere")) or \
           any(c_report[k] for k in ("added_to_nation", "placed_on_map", "unplaceable", "skipped_duplicate_elsewhere")):
            reports.append((n.get("name", ""), d_report, c_report))
    return reports


# ---------------------------------------------------------------------------
# Phase 4
# ---------------------------------------------------------------------------

def phase4_resolve_duplicates(dry_run):
    tiles_by_owner = _all_owned_tiles_by_nation()
    actions = []
    for name, tiles in tiles_by_owner.items():
        nation = mongo.db.nations.find_one({"name": name})
        if not nation:
            continue
        capital_tile = next((t for t in tiles if t.get("capital")), None)
        capital_coord = (capital_tile["q"], capital_tile["r"]) if capital_tile else None

        for key, nation_field in (("city", "cities"), ("district", "districts")):
            dups = _find_duplicates(tiles, key)
            nation_records = {r["_id"]: r for r in nation.get(nation_field, []) if isinstance(r, dict) and r.get("_id")}
            for oid, dup_tiles in dups.items():
                record = nation_records.get(oid, {})
                expected = record.get("type") if key == "city" else record.get("def_key")
                matching = [t for t in dup_tiles if _label(key, t[key]) == expected] if expected else []
                if len(matching) == 1:
                    keep = matching[0]
                    reason = "matches nation's recorded type/def_key"
                else:
                    def sort_key(t):
                        d = hex_distance(t["q"], t["r"], *capital_coord) if capital_coord else 0
                        return (d, t["q"], t["r"])
                    keep = sorted(dup_tiles, key=sort_key)[0]
                    reason = "closer to capital (tie-break — both copies matched or neither did)"
                for t in dup_tiles:
                    if t is keep:
                        continue
                    actions.append({
                        "nation": name, "kind": key, "id": oid,
                        "kept": (keep["q"], keep["r"]), "cleared": (t["q"], t["r"]), "reason": reason,
                    })
                    if not dry_run:
                        unset = {key: ""}
                        if t.get("capital"):
                            unset["capital"] = ""
                        mongo.db.hex_map_tiles.update_one({"_id": t["_id"]}, {"$unset": unset})
                        t.pop(key, None)
    return actions


# ---------------------------------------------------------------------------
# Phase 5
# ---------------------------------------------------------------------------

def phase5_relocate_disconnected_districts(dry_run):
    tiles_by_owner = _all_owned_tiles_by_nation()
    moved, refunded = [], []

    for name, tiles in tiles_by_owner.items():
        nation = mongo.db.nations.find_one({"name": name})
        if not nation:
            continue

        # Authoritative per-tile check (shared with audit_disconnected_
        # districts.py — see _is_properly_placed's docstring): correctly
        # excludes the imperial-quarter claim, treats free_placement
        # districts and the capital-bootstrap rule as legal, and also
        # catches a district illegally sitting on the capital tile itself.
        disconnected = []
        disconnected_legal = {}
        for t in tiles:
            if not t.get("district") or t["district"].get("imperial"):
                continue
            def_key = t["district"].get("def_key", "")
            dd = mongo.db.district_defs.find_one({"key": def_key})
            if not dd:
                disconnected.append(t)
                continue
            ok, legal = _is_properly_placed(nation, tiles, t, dd)
            if not ok:
                disconnected.append(t)
                disconnected_legal[t["q"], t["r"]] = legal
        if not disconnected:
            continue

        prices = _base_prices()
        state = evaluate_nation_state(nation)
        weights = _weights_from_net(
            state["net_production"], state["stockpiles"], prices, state["money_income"],
            active_resources=state.get("active_resources"), money_stock=state.get("money"),
        )
        reserved = set()

        for t in disconnected:
            d = t["district"]
            def_key = d.get("def_key", "")
            coord = (t["q"], t["r"])
            dd = mongo.db.district_defs.find_one({"key": def_key})
            if not dd:
                refunded.append({
                    "nation": name, "def_key": def_key, "id": d.get("id"), "coord": coord,
                    "reason": "no matching district_defs entry", "cost": {},
                })
                continue

            # Reuse the same self-excluded legal-placement computed during
            # detection above — recomputing from the raw `tiles` here would
            # let this tile anchor itself again (see _is_properly_placed's
            # docstring), and would miss that it's illegally on a capital.
            legal = disconnected_legal[coord]
            if reserved:
                legal = dict(legal)
                for k in ("legal_land_tiles", "legal_water_tiles", "legal_city_tiles"):
                    legal[k] = [x for x in legal.get(k, []) if x["coord"] not in reserved]

            new_coord, rationale = _pick_district_tile(legal, dd, def_key, weights, prices)
            if new_coord:
                reserved.add(tuple(new_coord))
                moved.append({
                    "nation": name, "def_key": def_key, "id": d.get("id"),
                    "from": coord, "to": new_coord, "rationale": rationale,
                })
                if not dry_run:
                    mongo.db.hex_map_tiles.update_one({"_id": t["_id"]}, {"$unset": {"district": ""}})
                    new_district = {
                        "id": d.get("id"), "def_key": def_key,
                        "display_name": dd.get("display_name", def_key), "type": "",
                    }
                    mongo.db.hex_map_tiles.update_one(
                        {"q": new_coord[0], "r": new_coord[1]},
                        {"$set": {"district": new_district}},
                    )
                    t.pop("district", None)
                    new_tile_entry = next(
                        (x for x in tiles if (x["q"], x["r"]) == tuple(new_coord)), None
                    )
                    if new_tile_entry is not None:
                        new_tile_entry["district"] = new_district
            else:
                cost = dd.get("cost", {})
                refunded.append({
                    "nation": name, "def_key": def_key, "id": d.get("id"), "coord": coord,
                    "reason": "no legal tile available anywhere", "cost": cost,
                })
                if not dry_run:
                    storage = _refund_into(nation, cost)
                    mongo.db.nations.update_one(
                        {"_id": nation["_id"]},
                        {"$pull": {"districts": {"_id": d.get("id")}}, "$set": {"resource_storage": storage}},
                    )
                    mongo.db.hex_map_tiles.update_one({"_id": t["_id"]}, {"$unset": {"district": ""}})
                    t.pop("district", None)

    return moved, refunded


def main():
    apply = "--apply" in sys.argv
    print(f"{'APPLYING' if apply else 'DRY RUN — nothing written'}\n")

    print("=== Phase 1: refund illegal Vandadorian Citadels ===")
    p1, lost_capital_nations, removed_tile_ids = phase1_refund_illegal_vandadorian_citadels(dry_run=not apply)
    for a in p1:
        print(f"  {a['nation']}: remove citadel id={a['id']} at tiles {a['tiles']}, refund {a['refund']}")
    if not p1:
        print("  (none found)")
    print()

    print("=== Phase 2: assign fallback capital where phase 1 removed the only one ===")
    p2 = phase2_assign_fallback_capital(lost_capital_nations, removed_tile_ids, dry_run=not apply)
    for a in p2:
        print(f"  {a['nation']}: new capital -> {a['new_capital']}")
    if not p2:
        print("  (not needed)")
    print()

    print("=== Phase 3: sync missing nation<->map placements ===")
    p3 = phase3_sync_missing_placements(dry_run=not apply)
    for name, d_report, c_report in p3:
        print(f"  {name}:")
        for item in d_report["added_to_nation"]:
            print(f"    district ADD TO NATION: {item['def_key']} (id={item['id']})")
        for item in d_report["placed_on_map"]:
            print(f"    district PLACE ON MAP: {item['def_key']} (id={item['id']}) at {item['coord']}")
        for item in d_report["unplaceable"]:
            print(f"    district UNPLACEABLE: {item['def_key']} (id={item['id']})")
        for item in c_report["added_to_nation"]:
            print(f"    city ADD TO NATION: {item['type']} (id={item['id']})")
        for item in c_report["placed_on_map"]:
            print(f"    city PLACE ON MAP: {item['type']} (id={item['id']}) at {item['coord']}")
        for item in c_report["unplaceable"]:
            print(f"    city UNPLACEABLE: {item['type']} (id={item['id']})")
    if not p3:
        print("  (none needed)")
    print()

    print("=== Phase 4: resolve duplicate city/district placements ===")
    p4 = phase4_resolve_duplicates(dry_run=not apply)
    for a in p4:
        print(f"  {a['nation']}: {a['kind']} id={a['id']} — keep {a['kept']}, clear {a['cleared']} ({a['reason']})")
    if not p4:
        print("  (none found)")
    print()

    print("=== Phase 5: relocate disconnected districts ===")
    moved, refunded = phase5_relocate_disconnected_districts(dry_run=not apply)
    for m in moved:
        print(f"  MOVED {m['nation']}: {m['def_key']} (id={m['id']}) {m['from']} -> {m['to']} ({m['rationale']})")
    for r in refunded:
        print(f"  REFUNDED+REMOVED {r['nation']}: {r['def_key']} (id={r['id']}) at {r['coord']} — {r['reason']}, refund {r['cost']}")
    if not moved and not refunded:
        print("  (none found)")
    print()

    if apply:
        bump_tile_version()
        print("Bumped hex map tile version so clients refetch.")
    else:
        print("Re-run with --apply to actually perform these changes.")


if __name__ == "__main__":
    main()
