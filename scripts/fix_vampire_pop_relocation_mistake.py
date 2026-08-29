"""
Fixes a mistake in scripts/relocate_vampire_pops_from_immune_nations.py:
that script physically MOVED 11 pop documents out of Sundralund and Alturus
into other nations (changing pop.nation) to represent "where their vampires
came from" — but that incorrectly transferred population between nations.
Sundralund and Alturus should have kept those citizens; only the disease
itself should have relocated.

This script, for each of the 11 originally-mistaken pops:
  1. Moves the pop back to its ORIGINAL nation (Sundralund or Alturus).
  2. Cures it of Vampirism directly: pulls the Vampirism id out of its
     `diseases` array and restores its race from `pre_disease_race`
     unconditionally. This does NOT use the shared cure_pop() helper's
     "only restore race once the diseases array is fully empty" rule,
     because 3 of these 11 pops also separately carry Mire Madness
     [Apocalyptic] (confirmed changes_race=False) — Vampirism was
     unambiguously the only disease holding these pops in a derived race,
     so restoring immediately is correct here even though Mire Madness
     remains. Mire Madness itself is left completely untouched either way.

Then, for each of the (up to 9 distinct) nations that wrongly received a
pop, it infects that same NUMBER of the nation's OWN existing pops with
Vampirism via the real infect_random_pops (Crimson Mirelight wrongly
received 3 pops, so gets 3 fresh infections of its own; every other
destination got exactly 1) — representing the disease actually having
spread there, on a citizen who was always that nation's own.

Everything is looked up by ObjectId, not by name, to avoid any risk from
special characters in nation names (e.g. "Sema'ayawi").

Dry run by default — prints the plan without writing. Pass --apply to
actually perform the mongo writes.

Run from the project root:
    python -m scripts.fix_vampire_pop_relocation_mistake            (dry run)
    python -m scripts.fix_vampire_pop_relocation_mistake --apply    (for real)
"""
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from bson import ObjectId
from app_core import mongo
from helpers.disease_helpers import infect_random_pops

SUNDRALUND_ID = "67ef7e63e311e8b3699a1b1c"
ALTURUS_ID = "67ef6d4698867826cf7962fa"

# pop_id -> the nation it originally belonged to (before the mistaken move)
POP_ORIGINAL_NATION = {
    "68901d4782e2d06e5947ac67": SUNDRALUND_ID,
    "67fd5104deb4f9b5b2e701f5": SUNDRALUND_ID,
    "683bcb0b27e910ffb2b045f9": SUNDRALUND_ID,
    "67f1d51af8544b5fac9cf906": SUNDRALUND_ID,
    "683bcb0e27e910ffb2b045fb": SUNDRALUND_ID,
    "68901d3e82e2d06e5947ac63": SUNDRALUND_ID,
    "67f1b82994b3bca289eebd67": ALTURUS_ID,
    "67f1b63f58b88de96ed5640a": ALTURUS_ID,
    "67f2fdadaf8ba078d9f5fe22": ALTURUS_ID,
    "68218f7e6a8460b8e920c984": ALTURUS_ID,
    "68d071f3069fd1459f581c8e": ALTURUS_ID,
}


def main():
    apply = "--apply" in sys.argv

    disease = mongo.db.diseases.find_one({"name": "Vampirism"})
    if not disease:
        print("Vampirism disease not found — aborting.")
        return
    did = str(disease["_id"])

    name_cache = {}
    def name_of(nation_id_str):
        if nation_id_str not in name_cache:
            n = mongo.db.nations.find_one({"_id": ObjectId(nation_id_str)}, {"name": 1})
            name_cache[nation_id_str] = n["name"] if n else f"<unknown {nation_id_str}>"
        return name_cache[nation_id_str]

    print(f"{'APPLYING' if apply else 'DRY RUN — nothing written'}\n")

    wrong_nation_counts = defaultdict(int)
    moves = []

    for pop_id_str, original_nation_id in POP_ORIGINAL_NATION.items():
        pop = mongo.db.pops.find_one({"_id": ObjectId(pop_id_str)})
        if not pop:
            print(f"  WARNING: pop {pop_id_str} not found — skipping.")
            continue

        current_nation_id = pop.get("nation")
        if current_nation_id == original_nation_id:
            print(f"  WARNING: pop {pop_id_str} is already back in {name_of(original_nation_id)} — skipping (already fixed?).")
            continue
        if did not in (pop.get("diseases") or []):
            print(f"  WARNING: pop {pop_id_str} no longer carries Vampirism — skipping.")
            continue

        pre_race = pop.get("pre_disease_race")
        remaining_diseases = [d for d in pop.get("diseases", []) if d != did]
        wrong_nation_name = name_of(current_nation_id)

        print(
            f"  pop {pop_id_str}: {wrong_nation_name} -> {name_of(original_nation_id)} (moved back), "
            f"cured of Vampirism"
            + (f", race restored" if pre_race else "")
            + (f", keeps Mire Madness" if remaining_diseases else "")
        )

        wrong_nation_counts[current_nation_id] += 1
        moves.append((pop["_id"], original_nation_id, pre_race))

    if apply:
        for pop_oid, original_nation_id, pre_race in moves:
            update = {
                "$set": {"nation": original_nation_id},
                "$pull": {"diseases": did},
            }
            if pre_race:
                update["$set"]["race"] = pre_race
                update["$unset"] = {"pre_disease_race": ""}
            mongo.db.pops.update_one({"_id": pop_oid}, update)

    print("\nInfecting each wrongly-visited nation's OWN pops with Vampirism (one per originally-misrouted pop):")
    for nation_id_str, count in wrong_nation_counts.items():
        if apply:
            infected = infect_random_pops(nation_id_str, disease, count)
            print(f"  {name_of(nation_id_str)}: infected {infected}/{count} of its own pops.")
        else:
            print(f"  {name_of(nation_id_str)}: would infect {count} of its own pops.")

    if not apply:
        print("\nRe-run with --apply to actually perform these changes.")


if __name__ == "__main__":
    main()
