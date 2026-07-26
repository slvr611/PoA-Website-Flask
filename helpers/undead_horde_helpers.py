"""Helpers for the "undead horde" nation archetype.

A nation qualifies when its primary race carries BOTH the "Ravenous"
(positive_trait) and "Mindless" (negative_trait) traits together — this
exact combination, not either trait alone, is what triggers the full
archetype (forced job, blanket action freeze, blocked resource production/
consumption, massively increased AI aggression). The individual traits also
carry their own ordinary per-trait law modifiers (stability-loss cap, civil
war chance floor, compliance shift) via json-data/schemas/races.json, exactly
like every other race trait — those apply even if a race only had one of the
two traits, since that mechanism is generic across all traits.

Currently the only races with this combination are those derived from the
"Undead Plague" disease (see helpers/disease_helpers.py's
convert_pop_to_accepted/get_or_create_derived_race — that disease's
race_positive_trait/race_negative_trait fields are set to "Ravenous"/
"Mindless"), but nothing here is disease-specific: any race manually assigned
both traits gets the same behavior.
"""

from bson.objectid import ObjectId
from app_core import mongo

UNDEAD_HORDE_JOB_KEY = "undead_horde"


def nation_is_undead_horde(nation):
    """True when `nation`'s primary race has positive_trait == "Ravenous"
    AND negative_trait == "Mindless" together."""
    if not nation:
        return False
    race_id = nation.get("primary_race")
    if not race_id:
        return False
    try:
        race = mongo.db.races.find_one(
            {"_id": ObjectId(str(race_id))},
            {"positive_trait": 1, "negative_trait": 1},
        )
    except Exception:
        return False
    if not race:
        return False
    return race.get("positive_trait") == "Ravenous" and race.get("negative_trait") == "Mindless"


def collect_undead_horde_job(target):
    """For an undead-horde nation, force every pop onto a single synthetic
    "Undead Horde" job, replacing whatever jobs would otherwise be assigned.

    Mirrors the disease forced-job pattern (see
    helpers/disease_helpers.collect_disease_effects): computed fresh at
    calculation time and never written into nation["jobs"]. The job has no
    production/upkeep of its own — undead-horde nations have all resource
    production/consumption separately blocked (see
    calculations/compute_functions.compute_resource_production/
    compute_resource_consumption), so the job's own output is moot.

    Returns (job_details, jobs_assigned); both are empty dicts when the
    nation doesn't qualify or has no pops. Callers should REPLACE (not
    merge) their normal job calculation with these when non-empty.
    """
    if not nation_is_undead_horde(target):
        return {}, {}
    pop_count = target.get("pop_count", 0) or 0
    if pop_count <= 0:
        return {}, {}
    job_details = {
        UNDEAD_HORDE_JOB_KEY: {
            "display_name": "Undead Horde",
            "production": {},
            "upkeep": {},
        }
    }
    jobs_assigned = {UNDEAD_HORDE_JOB_KEY: pop_count}
    return job_details, jobs_assigned
