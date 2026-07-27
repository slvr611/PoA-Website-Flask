"""
Seed the "Hag's Suffering" disease and simulate exactly one tick of its
spread + natural-cure mechanics, scoped ONLY to this disease.

What this does, in order:
  1. Creates the "Hag's Suffering" disease (reuses it if it already exists —
     see DISEASE_DOC_DEFAULTS below for the placeholder stats used, since
     these weren't specified up front; adjust via the disease's admin edit
     page afterward if they need tuning).
  2. Infects the requested starting pops for real (sets pop.disease directly,
     same as infect_pop() does for any real disease):
       - 3 pops in Taksi-Reborn
       - 1 pop in The Flow's Blade
       - 1 pop in Lumithal Aislid Alliance
       - 1 pop in Tychi
  3. Runs ONE tick's worth of nation_disease_spread_tick +
     nation_disease_natural_cure_tick for just these 4 nations (the same
     functions the real tick uses), and persists the result via the normal
     system_request_change/system_approve_change pipeline so it shows up in
     change history like a real tick would.
  4. Simulates the two CROSS-disease tick functions — disease cure progress
     and job-death rolls — but scoped to Hag's Suffering only. The real
     production functions (disease_cure_cross_tick, disease_job_death_tick
     in helpers/tick_helpers.py) scan every disease in the database with no
     per-disease filter, so calling them verbatim here would also process
     every OTHER real disease's cure progress / job deaths — exactly what
     "without doing the same for any other diseases" rules out. Instead,
     simulate_cure_tick_for_disease()/simulate_job_death_tick_for_disease()
     below reimplement the same per-disease logic (same helper calls, same
     change-request pattern) but hard-scoped to Hag's Suffering's own _id,
     so no other disease's document or infected pops are ever touched.

IMPORTANT — this is a REAL simulation against REAL nation/pop data, not a
dry run. In particular, nation_disease_spread_tick's spread roll can (as
designed, same as any real disease) land on a neighboring nation within 5
hexes rather than internally — if that happens it infects one real pop in
that other, unrelated nation. This is reported below if it happens; it is
not a bug, just how the spread mechanic actually works. infectivity is set
to "Low" (the lowest tier) specifically to keep this a small, unlikely
possibility for this one-tick test.

Re-running this script after the disease already exists will infect
ADDITIONAL new pops on top of the original counts, since already-infected
pops are excluded as candidates — this script is meant to be run once.

Run from the project root: python scripts/seed_hags_suffering_tick.py
"""

import random
from copy import deepcopy

from app_core import mongo
from helpers.data_helpers import get_data_on_category
from calculations.field_calculations import calculate_all_fields
from helpers.change_helpers import system_request_change, system_approve_change
from helpers.disease_helpers import (
    infect_pop, get_global_infection_counts, get_global_accepted_count, get_difficulty_settings,
)
from helpers.tick_helpers import nation_disease_spread_tick, nation_disease_natural_cure_tick

DISEASE_NAME = "Hag's Suffering"

# nation name -> number of starting pops to infect
TARGET_INFECTIONS = {
    "Taksi-Reborn": 3,
    "The Flow's Blade": 1,
    "Lumithal Aislid Alliance": 1,
    "Tychi": 1,
}

# Placeholder stats — none of these were specified by the requester. Adjust
# via the disease's admin edit page once real balance numbers are decided.
DISEASE_DOC_DEFAULTS = {
    "name": DISEASE_NAME,
    "rating": "Intense",
    "job_type": "Hag-Touched",
    "infectivity": "Low",
    "difficulty": "Difficult",
    "natural_cure_chance": 0.05,
    "job_production": [],
    "job_upkeep": [],
    "job_death_chance": 0,
    "changes_race": False,
}


def get_or_seed_disease():
    existing = mongo.db.diseases.find_one({"name": DISEASE_NAME})
    if existing:
        print(f"Disease '{DISEASE_NAME}' already exists ({existing['_id']}) — reusing it.")
        return existing

    doc = dict(DISEASE_DOC_DEFAULTS)
    inserted_id = mongo.db.diseases.insert_one(doc).inserted_id
    doc["_id"] = inserted_id
    print(f"Seeded new disease '{DISEASE_NAME}' ({inserted_id}) with placeholder stats:")
    for k, v in DISEASE_DOC_DEFAULTS.items():
        if k != "name":
            print(f"  {k}: {v}")
    print("  (These are placeholders — tune them via the disease's admin edit page.)")
    return doc


def infect_starting_pops(nation_name, count, disease):
    nation = mongo.db.nations.find_one({"name": nation_name})
    if not nation:
        print(f"  !! Nation '{nation_name}' not found — skipping.")
        return

    candidates = list(mongo.db.pops.find({
        "nation": str(nation["_id"]),
        "slave": {"$ne": True},
        "$or": [{"disease": {"$exists": False}}, {"disease": {"$in": [None, ""]}}],
    }))
    if len(candidates) < count:
        print(f"  !! {nation_name} only has {len(candidates)} eligible uninfected pops "
              f"(need {count}) — infecting all available.")
    random.shuffle(candidates)
    chosen = candidates[:count]
    for pop in chosen:
        infect_pop(pop, disease)
    ids = [str(p["_id"]) for p in chosen]
    print(f"  Infected {len(chosen)} pop(s) in {nation_name}: {ids}")


def simulate_tick_for_nation(nation_name):
    nation_schema, nation_db = get_data_on_category("nations")
    old_nation = nation_db.find_one({"name": nation_name})
    if not old_nation:
        return f"Nation '{nation_name}' not found — skipped.\n"

    old_nation.update(calculate_all_fields(old_nation, nation_schema, "nation"))
    new_nation = deepcopy(old_nation)

    result = ""
    result += nation_disease_spread_tick(old_nation, new_nation, nation_schema)
    result += nation_disease_natural_cure_tick(old_nation, new_nation, nation_schema)

    change_id = system_request_change(
        data_type="nations", item_id=old_nation["_id"], change_type="Update",
        before_data=old_nation, after_data=new_nation,
        reason=f"Hag's Suffering seed-tick simulation for {nation_name}",
    )
    if change_id is not None:
        system_approve_change(change_id)

    return result or f"No notable events for {nation_name} this tick.\n"


def simulate_cure_tick_for_disease(disease_id):
    """disease_cure_cross_tick's per-disease body, hard-scoped to one disease_id
    instead of scanning every uncured disease in the database."""
    disease = mongo.db.diseases.find_one({"_id": disease_id})
    if not disease:
        return ""
    disease_id_str = str(disease["_id"])
    disease_name = disease.get("name", disease_id_str)
    if disease.get("cured"):
        return f"{disease_name} is already marked cured — nothing further for the cure tick.\n"

    difficulty = get_difficulty_settings(disease)
    required = difficulty.get("required_progress", 0)

    global_counts = get_global_infection_counts()
    total_infected = global_counts.get(disease_id_str, 0) + get_global_accepted_count(disease)

    # Read-only scan of every nation's shared_quests for a contribution to
    # THIS disease's cure — mirrors the production function, which also
    # needs every nation to sum contributions. Never writes to a nation.
    contribution = 0
    contributors = 0
    for nation in mongo.db.nations.find({}, {"shared_quests": 1}):
        for quest in nation.get("shared_quests", []) or []:
            if isinstance(quest, dict) and str(quest.get("disease", "")) == disease_id_str:
                per_tick = quest.get("total_progress_per_tick", 0) or 0
                if per_tick > 0:
                    contribution += per_tick
                    contributors += 1

    if contribution <= 0:
        return f"{disease_name}: no shared-quest cure contribution this tick.\n"

    if total_infected < difficulty.get("min_infected_pops", 0):
        return (
            f"Cure research for {disease_name} is gated — {total_infected} infected pop(s), "
            f"needs {difficulty.get('min_infected_pops', 0)}.\n"
        )

    new_progress = min(disease.get("cure_progress", 0) + contribution, required)
    after_data = {**disease, "cure_progress": new_progress}
    completed = new_progress >= required
    if completed:
        after_data["cured"] = True

    change_id = system_request_change(
        data_type="diseases", item_id=disease["_id"], change_type="Update",
        before_data=deepcopy(disease), after_data=after_data,
        reason=f"Hag's Suffering seed-tick simulation: cure tick (+{contribution} progress from {contributors} nation(s))",
    )
    if change_id is not None:
        system_approve_change(change_id)

    result = f"{disease_name} cure progress: +{contribution} ({new_progress}/{required}).\n"
    if completed:
        result += (
            f"{disease_name}'s CURE HAS BEEN DISCOVERED — natural recovery is now twice as "
            f"likely for every infected pop, everywhere. The disease itself is not removed.\n"
        )
    return result


def simulate_job_death_tick_for_disease(disease_id):
    """disease_job_death_tick's per-disease body, hard-scoped to one disease_id
    instead of scanning every disease with job_death_chance > 0."""
    disease = mongo.db.diseases.find_one({"_id": disease_id})
    if not disease:
        return ""
    disease_id_str = str(disease["_id"])
    disease_name = disease.get("name", disease_id_str)
    death_chance = disease.get("job_death_chance", 0) or 0
    if death_chance <= 0:
        return f"{disease_name}: job_death_chance is 0 — no rolls made.\n"

    infected_pops = list(mongo.db.pops.find({"disease": disease_id_str}))
    died = 0
    for pop in infected_pops:
        if random.random() <= death_chance:
            before = {k: v for k, v in pop.items() if k != "_id"}
            change_id = system_request_change(
                data_type="pops", item_id=pop["_id"], change_type="Remove",
                before_data=before, after_data={},
                reason=f"Died from {disease_name} (job death chance) — Hag's Suffering seed-tick simulation",
            )
            if change_id is not None:
                system_approve_change(change_id)
                died += 1

    if died:
        return f"{disease_name}: {died} pop(s) died from job death chance.\n"
    return f"{disease_name}: {len(infected_pops)} infected pop(s) rolled, none died this tick.\n"


def main():
    print("=== Seeding Hag's Suffering ===")
    disease = get_or_seed_disease()

    print("\n=== Infecting starting pops ===")
    for nation_name, count in TARGET_INFECTIONS.items():
        infect_starting_pops(nation_name, count, disease)

    print("\n=== Simulating one tick (spread + natural cure, per nation) ===")
    summary = ""
    for nation_name in TARGET_INFECTIONS:
        summary += simulate_tick_for_nation(nation_name)

    print("\n=== Simulating cross-disease ticks (cure progress + job death), scoped to Hag's Suffering only ===")
    summary += simulate_cure_tick_for_disease(disease["_id"])
    summary += simulate_job_death_tick_for_disease(disease["_id"])

    print("\n=== Tick summary ===")
    print(summary.strip() or "(no notable events)")


if __name__ == "__main__":
    main()
