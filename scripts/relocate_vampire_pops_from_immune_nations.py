"""
One-time data fix: Sundralund, Eternal Chancellorship of the Black Scythe of
Alturus, and Jinying are being made immune to Vampirism (nation.disease_immunities).
Their existing vampire-infected pops need to move elsewhere first so the
disease's population doesn't just vanish.

Findings from tick-summary history + live DB (see conversation for detail):
  - Jinying: 0 currently infected pops. Nothing to do.
  - Sundralund: 6 infected pops. The most recent tick (2026-08-11) recorded
    exactly 3 fresh cross-nation spread events landing on it:
        "Vampirism has spread from Cha'saka to Sundralund!"
        "Vampirism has spread from Radhan to Sundralund!"
        "Vampirism has spread from The Shattered Accolade to Sundralund!"
    Per-pop provenance isn't tracked (infect_pop never records a source), so
    any 3 of Sundralund's 6 infected pops are interchangeable stand-ins for
    those 3 events. The other 3 have no tick history at all (predate the
    available archive).
  - Alturus: 5 infected pops, but its ENTIRE vampire history in the archive
    is the old, now-removed vampirism_tick mechanic ("has gained a vampire",
    a nation-native chance tied to being in the Twilight Mire region) — not
    the current cross-nation spread mechanic. No source nation exists for
    these at all.

User's decision (see conversation):
  1. For 3 of Sundralund's pops: re-roll a new external spread target from
     each of Cha'saka/Radhan/The Shattered Accolade's own perspective — i.e.
     replay pick_external_spread_target(source_nation, disease) — but with
     Sundralund/Alturus/Jinying excluded from the candidate pool, since
     those are the nations becoming immune. This is NOT "send it back to
     the literal source" — it's "as if the original spread roll had picked
     a different, non-immune target."
  2. For the remaining 8 pops (3 in Sundralund with no known source, 5 in
     Alturus): distribute them across other nations in the Twilight Mire
     region (excluding Sundralund/Alturus/Jinying) — not a traced origin,
     just a thematically-plausible reassignment matching where vampirism
     has always clustered (Alturus's own now-removed mechanic was itself
     tied to that region).

This is a REAL simulation against REAL pop data. Dry run by default — prints
every planned move without writing anything. Pass --apply to actually
perform the mongo writes (a plain {"$set": {"nation": new_id}} per pop,
same as execute_disease_civil_war's pop-move — pops carry no other
nation-specific fields that need adjusting).

Run from the project root:
    python scripts/relocate_vampire_pops_from_immune_nations.py            (dry run)
    python scripts/relocate_vampire_pops_from_immune_nations.py --apply    (for real)
"""
import sys
import random

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app_core import mongo
from helpers.disease_helpers import nation_accepts_disease
from helpers.hex_map_helpers import get_nations_within_distance
from helpers.trade_route_helpers import get_connectable_nations

IMMUNE_NATION_NAMES = [
    "Sundralund",
    "Eternal Chancellorship of the Black Scythe of Alturus",
    "Jinying",
]

# One pop each moves as a re-rolled spread event from its recorded source.
TRACEABLE_SOURCES = ["Cha'saka", "Radhan", "The Shattered Accolade"]

DISTRIBUTE_COUNTS = {
    # nation currently holding untraceable infected pops -> how many to move
    "Sundralund": 3,
    "Eternal Chancellorship of the Black Scythe of Alturus": 5,
}


def _reroll_external_target(source_name, disease, exclude_names, max_distance=5):
    """Same candidate-building logic as disease_helpers.pick_external_spread_target,
    but with an extra exclude_names set (the nations becoming immune) removed
    from the pool before the weighted random choice."""
    source = mongo.db.nations.find_one({"name": source_name})
    if not source:
        return None

    nearby = get_nations_within_distance(source_name, max_distance)
    if not nearby:
        return None

    connected = set()
    for route in mongo.db.trade_routes.find(
            {"status": {"$in": ["active", "ending"]},
             "$or": [{"nation_a": source_name}, {"nation_b": source_name}]},
            {"nation_a": 1, "nation_b": 1}):
        other = route.get("nation_a") if route.get("nation_b") == source_name else route.get("nation_b")
        if other:
            connected.add(other)
    try:
        connected |= {
            c.get("name") for c in get_connectable_nations(source_name, source.get("trade_speed", 1))
            if c.get("name")
        }
    except Exception:
        pass

    candidates, weights = [], []
    for cand_name in nearby:
        if cand_name in exclude_names:
            continue
        cand = mongo.db.nations.find_one({"name": cand_name})
        if not cand or nation_accepts_disease(cand, disease):
            continue
        candidates.append(cand)
        weights.append(2 if cand_name in connected else 1)
    if not candidates:
        return None
    return random.choices(candidates, weights=weights, k=1)[0]


def main():
    apply = "--apply" in sys.argv

    # Fixed seed so dry-run and --apply (two separate invocations) compute
    # the identical plan — this is a random-based reassignment, and the plan
    # shown to the user during dry-run must be exactly what --apply performs,
    # not a fresh independent roll.
    random.seed(20260827)

    disease = mongo.db.diseases.find_one({"name": "Vampirism"})
    if not disease:
        print("Vampirism disease not found — aborting.")
        return
    did = str(disease["_id"])

    twilight_mire = mongo.db.regions.find_one({"name": "Twilight Mire"})
    mire_id = str(twilight_mire["_id"]) if twilight_mire else None

    plan = []  # list of (pop_id, from_nation_name, to_nation_name)

    # --- Part 1: 3 re-rolled spread events from the recorded source nations ---
    sundralund = mongo.db.nations.find_one({"name": "Sundralund"})
    sund_pops = list(mongo.db.pops.find({"nation": str(sundralund["_id"]), "diseases": did}))
    already_planned_pop_ids = set()

    for source_name in TRACEABLE_SOURCES:
        target = _reroll_external_target(source_name, disease, IMMUNE_NATION_NAMES)
        if target is None:
            print(f"WARNING: no valid re-roll target found for spread from {source_name} — skipping this one.")
            continue
        remaining = [p for p in sund_pops if p["_id"] not in already_planned_pop_ids]
        if not remaining:
            print(f"WARNING: ran out of Sundralund pops to move for the {source_name} re-roll.")
            continue
        pop = random.choice(remaining)
        already_planned_pop_ids.add(pop["_id"])
        plan.append((pop["_id"], "Sundralund", target["name"], f"re-rolled spread from {source_name}"))

    # --- Part 2: distribute the untraceable pops across other Twilight Mire nations ---
    mire_pool = []
    if mire_id:
        mire_pool = [
            n["name"] for n in mongo.db.nations.find(
                {"region": mire_id, "name": {"$nin": IMMUNE_NATION_NAMES}}, {"name": 1}
            )
        ]
    if not mire_pool:
        print("WARNING: no other Twilight Mire nations found to distribute untraceable pops to — aborting part 2.")
    else:
        for nation_name, count in DISTRIBUTE_COUNTS.items():
            nation = mongo.db.nations.find_one({"name": nation_name})
            infected = list(mongo.db.pops.find({"nation": str(nation["_id"]), "diseases": did}))
            if nation_name == "Sundralund":
                infected = [p for p in infected if p["_id"] not in already_planned_pop_ids]
            pool_for_this_nation = infected[:count]
            if len(pool_for_this_nation) < count:
                print(f"WARNING: {nation_name} only has {len(pool_for_this_nation)} untraceable pops available, expected {count}.")
            for pop in pool_for_this_nation:
                dest_name = random.choice(mire_pool)
                already_planned_pop_ids.add(pop["_id"])
                plan.append((pop["_id"], nation_name, dest_name, "distributed to Twilight Mire (no traceable source)"))

    # --- Report / apply ---
    print(f"Planned {len(plan)} pop relocations ({'APPLYING' if apply else 'DRY RUN — nothing written'}):")
    for pop_id, from_name, to_name, reason in plan:
        print(f"  pop {pop_id}: {from_name} -> {to_name}  ({reason})")

    if not apply:
        print("\nRe-run with --apply to actually perform these moves.")
        return

    name_to_id = {}
    for _, _, to_name, _ in plan:
        if to_name not in name_to_id:
            n = mongo.db.nations.find_one({"name": to_name}, {"_id": 1})
            name_to_id[to_name] = str(n["_id"])

    for pop_id, from_name, to_name, reason in plan:
        mongo.db.pops.update_one({"_id": pop_id}, {"$set": {"nation": name_to_id[to_name]}})
    print(f"\nApplied {len(plan)} pop relocations.")


if __name__ == "__main__":
    main()
