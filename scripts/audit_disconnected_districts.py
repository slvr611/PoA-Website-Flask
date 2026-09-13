"""
Read-only audit for two related map-data problems reported live (the
"Twinborn Elitria" bug — see conversation/commit history around
helpers.ai_decision_helpers.sync_nation_cities/sync_nation_districts's new
duplicate-placement guard):

1. DUPLICATE PLACEMENTS — the same city/district id sitting on two
   different map tiles for the same nation. sync_nation_cities/
   sync_nation_districts's "Nation -> Map" direction used to be able to
   create these (fixed now, see their world_city_ids/world_district_ids
   guard), but that fix is not retroactive: an id that's already fully
   present within a nation's own tiles never goes through that check again,
   so existing duplicates are invisible to the reconcile tool and to
   sync_cities/sync_districts. This audit finds them directly.

   A duplicate can also be the ROOT CAUSE of an apparently-legal but
   actually-disconnected district: _compute_legal_placement only requires a
   new district to be adjacent to *some* existing building, not that the
   building trace back to the capital — so a ghost duplicate tile can act
   as a fake anchor that lets more districts be built in a pocket that has
   no real connection to the nation's territory. Confirmed live: Twinborn
   Elitria's "forge" and "ranch" districts are only adjacent to a duplicate
   copy of its "vandadorian_citadel" city; remove the duplicate and both
   districts are an isolated 2-tile pocket.

2. DISCONNECTED DISTRICTS — any NON-free-placement district tile with ZERO
   adjacent building tiles (no district or city on any of its 6 neighbors),
   checked against the CURRENT map (duplicates included). District defs
   with free_placement=True (currently just "outpost") are excluded
   entirely: that flag means _compute_legal_placement deliberately never
   requires them to be adjacent to anything (same rule cities get), so an
   isolated outpost is correct by design, not a bug.

   This is deliberately NOT "must trace back to the capital": this game
   explicitly allows non-contiguous nation territory (conquered exclaves
   via war, tiles claimed "via naval routes" per
   is_tile_legally_controllable's own docstring), so a whole captured
   city+district cluster sitting apart from a nation's capital is normal,
   not a bug — _compute_legal_placement's actual build-time rule only ever
   requires a new district to be adjacent to *some* existing building,
   never that that building trace back to the capital. A district that
   satisfied this rule at build time but is later left with literally no
   adjacent building (its only neighbor removed, captured away, or — see
   part 1 — a duplicate ghost that was never real to begin with) is the
   actual violation this checks for.

   For each one found, this proposes a legal relocation tile using the
   exact same scoring the AI itself uses when building a new district
   (_pick_district_tile), or reports that none exists (which would mean a
   refund-and-removal, mirroring how fix_city_and_capital_placement handles
   an unplaceable city) — never mutates anything.

Read-only: computes and prints everything; writes nothing. Cities are
exempt from this check by design (_compute_legal_placement never requires
a city to be adjacent to anything), so only districts are flagged in part 2
— but a duplicate CITY id is still reported in part 1, since it's corrupt
data either way and can itself be the fake anchor behind a part-2 finding.

Run from the project root:
    python -m scripts.audit_disconnected_districts
"""
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app_core import mongo
from calculations.field_calculations import _hex_neighbors, _compute_legal_placement
from helpers.ai_decision_helpers import (
    _pick_district_tile, _base_prices, evaluate_nation_state, _weights_from_net,
)


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
    """key: "city" or "district". Returns {id: [tile, ...]} for ids on 2+ tiles."""
    groups = defaultdict(list)
    for t in tiles:
        obj = t.get(key)
        if isinstance(obj, dict) and obj.get("id"):
            groups[obj["id"]].append(t)
    return {k: v for k, v in groups.items() if len(v) > 1}


def _label(key, obj):
    if key == "city":
        return obj.get("type", "generic")
    return obj.get("def_key", "")


def _has_adjacent_building(coord, building_coords_excluding_self):
    return any(n in building_coords_excluding_self for n in _hex_neighbors(*coord))


def _is_properly_placed(nation, tiles, t, dd):
    """Authoritative check, reusing _compute_legal_placement itself rather
    than re-deriving its adjacency/bootstrap/free-placement rules by hand:
    if this tile's district were empty right now, would _pick_district_tile
    ever offer this exact coordinate back as a legal spot for a district of
    this type? If yes, it's correctly placed even if a naive "does it touch
    another district/city" check would call it isolated — e.g. a lone
    district adjacent to a bare capital tile (capital=True, no city built
    there yet) is legal via _compute_legal_placement's own bootstrap rule,
    and a free_placement district (currently just "outpost") is legal
    anywhere in owned territory by design."""
    coord = (t["q"], t["r"])
    tiles_for_calc = [{**x, "district": None} if x is t else x for x in tiles]
    nation_local = dict(nation)
    nation_local.pop("_legal_placement_cache", None)
    legal = _compute_legal_placement(nation_local, owned_tiles=tiles_for_calc)
    if dd.get("free_placement", False):
        pool = legal.get("legal_city_tiles", [])
    else:
        pool = legal.get("legal_land_tiles", []) + legal.get("legal_water_tiles", [])
    return coord in {c["coord"] for c in pool}, legal


def main():
    tiles_by_owner = _all_owned_tiles_by_nation()
    nations_by_name = {n.get("name", ""): n for n in mongo.db.nations.find() if n.get("name")}

    print(f"{len(tiles_by_owner)} nations own at least one tile.\n")

    dup_nation_count = 0
    dup_total = 0
    disconnected_total = 0
    relocatable_total = 0

    for name, tiles in sorted(tiles_by_owner.items()):
        nation = nations_by_name.get(name)
        if not nation:
            continue

        dup_cities = _find_duplicates(tiles, "city")
        dup_districts = _find_duplicates(tiles, "district")

        building_coords = {
            (t["q"], t["r"]) for t in tiles if t.get("city") or t.get("district")
        }

        # Authoritative: for each district tile, would _compute_legal_placement
        # (with this tile's own district hidden from the calc) offer this
        # exact coordinate back as legal? If not, it's genuinely disconnected —
        # see _is_properly_placed's docstring for why this beats a hand-rolled
        # adjacency check (capital bootstrap, free_placement, water/land are
        # all handled correctly for free by reusing the real function).
        disconnected_districts = []
        disconnected_legal = {}
        for t in tiles:
            if not t.get("district") or t["district"].get("imperial"):
                # The imperial-quarter claim (tile.district.imperial=True) is
                # a separate single-instance mechanism tied to
                # nation.imperial_district, not a real districts-array
                # entry — no def_key, not subject to placement rules at
                # all. sync_nation_districts excludes it the same way.
                continue
            def_key = t["district"].get("def_key", "")
            dd = mongo.db.district_defs.find_one({"key": def_key})
            if not dd:
                disconnected_districts.append(t)
                continue
            ok, legal = _is_properly_placed(nation, tiles, t, dd)
            if not ok:
                disconnected_districts.append(t)
                disconnected_legal[t["q"], t["r"]] = legal

        if not dup_cities and not dup_districts and not disconnected_districts:
            continue

        print(f"=== {name} ===")

        if dup_cities or dup_districts:
            dup_nation_count += 1
        for key, dups in (("city", dup_cities), ("district", dup_districts)):
            for cid, dup_tiles in dups.items():
                dup_total += 1
                labels = {_label(key, t[key]) for t in dup_tiles}
                mismatch = " ** MISMATCHED TYPE/DEF_KEY — likely a stale ghost from an old placement **" if len(labels) > 1 else ""
                locs = ", ".join(
                    f"({t['q']},{t['r']}) [{_label(key, t[key])}]" for t in dup_tiles
                )
                print(f"  DUPLICATE {key} id={cid}: {locs}{mismatch}")
                # Report which copies would be isolated if the OTHER copies
                # in this duplicate group didn't exist — helps a human tell
                # which copy is the real one vs. the ghost.
                for t in dup_tiles:
                    other_coords = {(o["q"], o["r"]) for o in dup_tiles if o is not t}
                    without_others = building_coords - other_coords
                    has_real_anchor = _has_adjacent_building(
                        (t["q"], t["r"]), without_others - {(t["q"], t["r"])}
                    )
                    tag = "has its own adjacent building, independent of the other copy" if has_real_anchor \
                        else "ONLY reachable via the other copy — likely the ghost"
                    print(f"      ({t['q']},{t['r']}): {tag}")

        if disconnected_districts:
            prices = _base_prices()
            state = evaluate_nation_state(nation)
            weights = _weights_from_net(
                state["net_production"], state["stockpiles"], prices, state["money_income"],
                active_resources=state.get("active_resources"), money_stock=state.get("money"),
            )

            for t in disconnected_districts:
                disconnected_total += 1
                d = t["district"]
                def_key = d.get("def_key", "")
                coord = (t["q"], t["r"])
                dd = mongo.db.district_defs.find_one({"key": def_key})
                if t.get("capital"):
                    # A separate, related violation this same check happens
                    # to also catch: a capital hex may only ever host a
                    # city, never a district (_compute_legal_placement
                    # excludes capital tiles from legal_land_tiles/
                    # legal_water_tiles for exactly this reason) — so a
                    # district sitting on one is illegal regardless of
                    # adjacency, and the fix is identical (relocate it).
                    print(f"  ILLEGAL district ON THE CAPITAL TILE (capitals may only host a city): {def_key} (id={d.get('id')}) at {coord}")
                else:
                    print(f"  DISCONNECTED district (not legally reachable from any anchor): {def_key} (id={d.get('id')}) at {coord}")
                if not dd:
                    print("      no matching district_defs entry — cannot propose a relocation")
                    continue
                # Reuse the same self-excluded legal-placement computed
                # during detection — recomputing from the raw `tiles` here
                # would let this tile anchor itself again (see
                # _is_properly_placed's docstring).
                legal = disconnected_legal[coord]
                new_coord, rationale = _pick_district_tile(legal, dd, def_key, weights, prices)
                if new_coord:
                    relocatable_total += 1
                    print(f"      proposed relocation -> {new_coord} ({rationale})")
                else:
                    cost = dd.get("cost", {})
                    print(f"      no legal tile available anywhere — would refund {cost} and remove")
        print()

    print("--- Summary ---")
    print(f"Nations with a duplicate city/district id: {dup_nation_count}")
    print(f"Total duplicate id groups found: {dup_total}")
    print(f"Total genuinely disconnected districts found (zero adjacent buildings): {disconnected_total}")
    print(f"  of which a legal relocation tile exists: {relocatable_total}")
    print(f"  of which no legal tile exists (would need refund+removal): {disconnected_total - relocatable_total}")
    print("\nThis tool is read-only — nothing was written. No --apply mode exists yet;")
    print("resolving these is a follow-up decision once these findings are reviewed.")


if __name__ == "__main__":
    main()
