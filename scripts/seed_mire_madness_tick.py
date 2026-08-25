"""
Seed the "Mire Madness [Apocalyptic]" disease and simulate exactly one
session's worth of its effects, scoped ONLY to this disease.

What this does, in order:
  1. One-time, idempotent migration: every pop's single `disease` field
     becomes a `diseases` array (re-running this script is safe — the
     migration query only matches pops that still have the old field).
  2. Creates the "Mire Madness [Apocalyptic]" disease (reuses it if it
     already exists).
  3. Infects the requested starting pops for real, via the same
     infect_random_pops() the admin "Infect" button uses — this disease's
     infects_diseased_pops=True means it can land on pops that already
     carry another disease (co-infection, not replacement).
  4. Runs ONE session's worth of nation_disease_spread_tick +
     nation_disease_natural_cure_tick + nation_disease_effects_tick (the
     new windfall/compliance mechanic) for the affected nations, persisted
     via system_request_change/system_approve_change so it shows up in
     change history like a real tick would.
  5. Simulates the two cross-disease tick functions — cure progress and
     job-death rolls — scoped to Mire Madness only (the production
     functions scan every disease in the database with no per-disease
     filter; calling them verbatim here would also process every OTHER
     real disease, which "scoped only to this disease" rules out).

Zhǎo Nèi Lù is deliberately excluded from the starting-infection list: it
has zero real pop documents despite its nation.pop_count field showing 4 —
a pre-existing data problem unrelated to this disease, flagged in the
summary output rather than silently skipped.

IMPORTANT — this is a REAL simulation against REAL nation/pop data, not a
dry run. Same caveats as every other disease seed script: a spread roll can
land on a neighboring nation within 5 hexes rather than internally, and
random resource windfalls / vassal compliance changes are real writes.

Run from the project root: python scripts/seed_mire_madness_tick.py
"""

import random
import sys
from copy import deepcopy

from bson import ObjectId

# Windows consoles default to cp1252, which can't print some nation names
# (e.g. "Zhǎo Nèi Lù") — force UTF-8 stdout so the summary never crashes
# partway through printing, regardless of the terminal's default encoding.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app_core import mongo
from helpers.data_helpers import get_data_on_category
from calculations.field_calculations import calculate_all_fields
from helpers.change_helpers import system_request_change, system_approve_change
from helpers.disease_helpers import (
    infect_random_pops, get_global_infection_counts, get_global_accepted_count, get_difficulty_settings,
)
from helpers.tick_helpers import (
    nation_disease_spread_tick, nation_disease_natural_cure_tick, nation_disease_effects_tick,
)

DISEASE_NAME = "Mire Madness [Apocalyptic]"
TWILIGHT_MIRE_REGION_NAME = "Twilight Mire"

# nation name -> number of starting pops to infect
TARGET_INFECTIONS = {
    "Eternal Chancellorship of the Black Scythe of Alturus": 2,
    "Sundralund": 4,
    "The Shepard Core": 3,
    "Cha'saka": 2,
    "Sanguine Lumineux": 1,
    "Crimson Mirelight": 4,
    "Daksha D'mae": 3,
    "Albino Valonmaa": 2,
    "Undying Rivo": 4,
    "Atomosi Syndicate": 3,
    "Flayedland": 2,
}

# Confirmed live: pop_count says 4 but zero real pop documents exist. Can't
# seed infections there — flagged explicitly in the summary instead of
# being silently dropped from TARGET_INFECTIONS above.
SKIPPED_NATION_NAME = "Zhǎo Nèi Lù"
SKIPPED_NATION_REASON = "pop_count field says 4 but it has zero real pop documents (pre-existing data problem)"


def migrate_disease_field_to_array():
    """One-time migration: pop.disease (single string) -> pop.diseases
    (array). Idempotent — only matches pops that still have the old field,
    so re-running this script is safe."""
    migrated = 0
    cleared_empty = 0
    for pop in mongo.db.pops.find({"disease": {"$exists": True}}, {"disease": 1}):
        disease_val = pop.get("disease")
        if disease_val:
            mongo.db.pops.update_one(
                {"_id": pop["_id"]},
                {"$set": {"diseases": [str(disease_val)]}, "$unset": {"disease": ""}},
            )
            migrated += 1
        else:
            mongo.db.pops.update_one({"_id": pop["_id"]}, {"$unset": {"disease": ""}})
            cleared_empty += 1
    return migrated, cleared_empty


def get_or_seed_disease():
    """Returns (disease_doc, was_just_created). `was_just_created` is False
    when reusing an already-seeded disease — main() uses this to skip
    re-running infect_starting_pops, since infect_random_pops's candidates
    aren't scoped to "the original seed run" and re-infecting would add
    MORE than the intended starting counts on top of whatever's already
    infected."""
    existing = mongo.db.diseases.find_one({"name": DISEASE_NAME})
    if existing:
        print(f"Disease '{DISEASE_NAME}' already exists ({existing['_id']}) — reusing it, "
              f"skipping starting-infection seeding (already done).")
        return existing, False

    region = mongo.db.regions.find_one({"name": TWILIGHT_MIRE_REGION_NAME})
    if not region:
        raise RuntimeError(f"Region '{TWILIGHT_MIRE_REGION_NAME}' not found — cannot seed the disease.")

    doc = {
        "name": DISEASE_NAME,
        "rating": "Apocalyptic",
        "job_type": "Paranoid",
        "job_production": [{"_id": "sgc1", "key": "max_stability_gain_chance", "value": -0.05}],
        "job_upkeep": [],
        "natural_cure_chance": 0.02,
        "job_death_chance": 0.10,
        "infectivity": "Highly",
        "difficulty": "Impossible",
        "cure_progress": 0,
        "cured": False,
        "changes_race": False,
        "infects_diseased_pops": True,
        "restricted_region": str(region["_id"]),
        "outside_region_infectivity": "Low",
        "compliance_loss_at_max_infection": True,
        "random_resource_windfall_per_infected_pop": 1,
        "prosperity_role_condition": "Ravager",
        "override_job_production": [{"_id": "sgc2", "key": "max_stability_gain_chance", "value": 0.05}],
        "override_job_death_chance": 0.01,
        "stages": [],
    }
    inserted_id = mongo.db.diseases.insert_one(doc).inserted_id
    doc["_id"] = inserted_id
    print(f"Seeded new disease '{DISEASE_NAME}' ({inserted_id}) restricted to {TWILIGHT_MIRE_REGION_NAME} ({region['_id']}).")
    return doc, True


def infect_starting_pops(nation_name, count, disease):
    nation = mongo.db.nations.find_one({"name": nation_name})
    if not nation:
        print(f"  !! Nation '{nation_name}' not found — skipping.")
        return
    infected = infect_random_pops(str(nation["_id"]), disease, count)
    if infected < count:
        print(f"  !! {nation_name}: only {infected} of {count} requested pops were eligible.")
    print(f"  Infected {infected} pop(s) in {nation_name}.")


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
    result += nation_disease_effects_tick(old_nation, new_nation, nation_schema)

    change_id = system_request_change(
        data_type="nations", item_id=old_nation["_id"], change_type="Update",
        before_data=old_nation, after_data=new_nation,
        reason=f"Mire Madness seed-tick simulation for {nation_name}",
    )
    if change_id is not None:
        # old_nation/new_nation already went through calculate_all_fields
        # above, so this data is fully calculated — skip_recalculation=True
        # is both correct (matches system_approve_change's own documented
        # use case) and necessary: without it, check_no_other_changes
        # compares the freshly-recalculated old_nation against the DB's
        # not-yet-recalculated stored document (whichever fields only just
        # started differing because of this very infection, e.g. a newly
        # appearing disease job_details entry) and blocks the whole update.
        ok = system_approve_change(change_id, skip_recalculation=True)
        if not ok:
            result += f"!! WARNING: nation update for {nation_name} was blocked and NOT saved.\n"

    return result or f"No notable events for {nation_name} this session.\n"


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
        return f"{disease_name}: no shared-quest cure contribution this session.\n"

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
        reason=f"Mire Madness seed-tick simulation: cure tick (+{contribution} progress from {contributors} nation(s))",
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
    instead of scanning every disease with job_death_chance > 0 — including
    its prosperity_role-conditional override and same-pop double-queue guard."""
    disease = mongo.db.diseases.find_one({"_id": disease_id})
    if not disease:
        return ""
    disease_id_str = str(disease["_id"])
    disease_name = disease.get("name", disease_id_str)
    base_death_chance = disease.get("job_death_chance", 0) or 0
    if base_death_chance <= 0:
        return f"{disease_name}: job_death_chance is 0 — no rolls made.\n"

    role_condition = disease.get("prosperity_role_condition") or "None"
    override_death_chance = disease.get("override_job_death_chance")

    infected_pops = list(mongo.db.pops.find({"diseases": disease_id_str}))

    nation_roles = {}
    if role_condition != "None" and override_death_chance is not None:
        nation_object_ids = []
        for p in infected_pops:
            try:
                nation_object_ids.append(ObjectId(p.get("nation", "")))
            except Exception:
                continue
        for n in mongo.db.nations.find({"_id": {"$in": nation_object_ids}}, {"prosperity_role": 1}):
            nation_roles[str(n["_id"])] = n.get("prosperity_role")

    died = 0
    for pop in infected_pops:
        death_chance = base_death_chance
        if role_condition != "None" and override_death_chance is not None:
            if nation_roles.get(str(pop.get("nation", ""))) == role_condition:
                death_chance = override_death_chance
        if random.random() <= death_chance:
            before = {k: v for k, v in pop.items() if k != "_id"}
            change_id = system_request_change(
                data_type="pops", item_id=pop["_id"], change_type="Remove",
                before_data=before, after_data={},
                reason=f"Died from {disease_name} (job death chance) — Mire Madness seed-tick simulation",
            )
            if change_id is not None:
                system_approve_change(change_id)
                died += 1

    if died:
        return f"{disease_name}: {died} pop(s) died from job death chance.\n"
    return f"{disease_name}: {len(infected_pops)} infected pop(s) rolled, none died this session.\n"


def main():
    print("=== Migrating pop.disease -> pop.diseases ===")
    migrated, cleared_empty = migrate_disease_field_to_array()
    print(f"Migrated {migrated} infected pop(s) to the new array field; cleared {cleared_empty} empty field(s).")

    print("\n=== Seeding Mire Madness [Apocalyptic] ===")
    disease, was_just_created = get_or_seed_disease()

    if was_just_created:
        print("\n=== Infecting starting pops ===")
        for nation_name, count in TARGET_INFECTIONS.items():
            infect_starting_pops(nation_name, count, disease)
        print(f"  Skipped '{SKIPPED_NATION_NAME}': {SKIPPED_NATION_REASON}")

    print("\n=== Simulating one session (spread + natural cure + windfall/compliance, per nation) ===")
    summary = ""
    for nation_name in TARGET_INFECTIONS:
        summary += simulate_tick_for_nation(nation_name)

    print("\n=== Simulating cross-disease ticks (cure progress + job death), scoped to Mire Madness only ===")
    summary += simulate_cure_tick_for_disease(disease["_id"])
    summary += simulate_job_death_tick_for_disease(disease["_id"])

    print("\n=== Session summary ===")
    print(summary.strip() or "(no notable events)")


if __name__ == "__main__":
    main()
