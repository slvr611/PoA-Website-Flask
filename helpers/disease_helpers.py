"""Helpers for the modular disease system.

Diseases are admin-defined documents (the ``diseases`` collection). Infection
is stored per pop (``pop.disease`` = disease ObjectId string); nation-level
infected counts are always derived from pops. The forced disease job is
virtual: it is injected into ``job_details``/``jobs_assigned`` at calculation
time and never written into ``nation["jobs"]``, so edits to a disease document
propagate to every infected nation automatically.
"""

import random

from bson.objectid import ObjectId

from app_core import mongo, category_data

_DEFAULT_INFECTIVITY = {"base_chance": 0.10, "chance_per_infected": 0.02, "max_infected_pct": 0.25}
_DEFAULT_DIFFICULTY = {"required_progress": 100, "dc_change": 0, "min_infected_pops": 5}


# ---------------------------------------------------------------------------
# Settings lookups (schema enum `settings` dicts)
# ---------------------------------------------------------------------------

def _enum_settings(field_name, value, default):
    schema = category_data.get("diseases", {}).get("schema", {})
    settings = schema.get("properties", {}).get(field_name, {}).get("settings", {})
    result = settings.get(value)
    return dict(result) if isinstance(result, dict) else dict(default)


def get_infectivity_settings(disease):
    return _enum_settings("infectivity", disease.get("infectivity", ""), _DEFAULT_INFECTIVITY)


def get_difficulty_settings(disease):
    return _enum_settings("difficulty", disease.get("difficulty", ""), _DEFAULT_DIFFICULTY)


# ---------------------------------------------------------------------------
# Infection state (derived from pops)
# ---------------------------------------------------------------------------

def get_nation_infection_counts(nation_id):
    """Return {disease_id_str: infected_pop_count} for one nation."""
    counts = {}
    try:
        cursor = mongo.db.pops.aggregate([
            {"$match": {"nation": str(nation_id), "disease": {"$nin": [None, ""]}}},
            {"$group": {"_id": "$disease", "count": {"$sum": 1}}},
        ])
        for doc in cursor:
            counts[str(doc["_id"])] = doc["count"]
    except Exception:
        pass
    return counts


def get_global_infection_counts():
    """Return {disease_id_str: total_infected_pops} across all nations."""
    counts = {}
    try:
        cursor = mongo.db.pops.aggregate([
            {"$match": {"disease": {"$nin": [None, ""]}}},
            {"$group": {"_id": "$disease", "count": {"$sum": 1}}},
        ])
        for doc in cursor:
            counts[str(doc["_id"])] = doc["count"]
    except Exception:
        pass
    return counts


def get_global_accepted_count(disease):
    """Return the number of pops permanently converted to `disease`'s derived
    race across all nations ('accepted' pops — convert_pop_to_accepted clears
    their disease marker, so get_global_infection_counts alone misses them,
    even though they're still hosts of the disease's identity).

    Excludes pops that still carry the disease marker (mid-infection, not yet
    accepted — infect_pop flips race to the derived race immediately on
    infection, before the disease field is ever cleared) so this never
    overlaps with get_global_infection_counts's count of the same pop."""
    derived_ids = _derived_race_ids(disease)
    if not derived_ids:
        return 0
    try:
        return mongo.db.pops.count_documents({
            "race": {"$in": list(derived_ids)},
            "$or": [{"disease": {"$exists": False}}, {"disease": {"$in": [None, ""]}}],
        })
    except Exception:
        return 0


def get_accepted_counts_by_nation(disease):
    """Per-nation breakdown of get_global_accepted_count — {nation_id_str: count}
    of pops permanently converted to `disease`'s derived race with no active
    infection marker. Companion for display pages that need a per-nation
    "Infected Nations" table rather than just the global total."""
    derived_ids = _derived_race_ids(disease)
    if not derived_ids:
        return {}
    counts = {}
    try:
        cursor = mongo.db.pops.aggregate([
            {"$match": {
                "race": {"$in": list(derived_ids)},
                "$or": [{"disease": {"$exists": False}}, {"disease": {"$in": [None, ""]}}],
            }},
            {"$group": {"_id": "$nation", "count": {"$sum": 1}}},
        ])
        for doc in cursor:
            counts[str(doc["_id"])] = doc["count"]
    except Exception:
        pass
    return counts


def infection_counts_from_pops(pops):
    """Derive {disease_id_str: count} from an in-memory pops list."""
    counts = {}
    for pop in pops:
        disease_id = pop.get("disease") or ""
        if disease_id:
            counts[str(disease_id)] = counts.get(str(disease_id), 0) + 1
    return counts


def resolve_diseases(disease_ids):
    """Batch-fetch disease docs by id. Returns {disease_id_str: doc}."""
    oids = []
    for did in disease_ids:
        try:
            oids.append(ObjectId(str(did)))
        except Exception:
            continue
    if not oids:
        return {}
    try:
        return {str(d["_id"]): d for d in mongo.db.diseases.find({"_id": {"$in": oids}})}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Stages and job effects
# ---------------------------------------------------------------------------

def active_stage_index(disease, infected, pop_count):
    """Index into disease["stages"] of the active stage, or -1 for base.

    Active stage = the highest-threshold stage whose threshold_pct (percent of
    the nation's pops infected) has been reached.
    """
    stages = disease.get("stages") or []
    if not stages or pop_count <= 0:
        return -1
    pct = infected / pop_count * 100
    best_idx, best_threshold = -1, -1.0
    for idx, stage in enumerate(stages):
        if not isinstance(stage, dict):
            continue
        try:
            threshold = float(stage.get("threshold_pct") or 0)
        except (TypeError, ValueError):
            continue
        if threshold <= pct and threshold > best_threshold:
            best_idx, best_threshold = idx, threshold
    return best_idx


def get_stage(disease, stage_idx):
    stages = disease.get("stages") or []
    if 0 <= stage_idx < len(stages) and isinstance(stages[stage_idx], dict):
        return stages[stage_idx]
    return None


def disease_job_key(disease):
    return "disease_" + str(disease.get("_id", ""))


def kv_list_to_dict(lst):
    """[{key, value}, ...] → {key: value}, skipping blank keys."""
    result = {}
    for entry in lst or []:
        if isinstance(entry, dict) and entry.get("key"):
            try:
                result[entry["key"]] = float(entry.get("value") or 0)
            except (TypeError, ValueError):
                continue
    return result


def collect_disease_effects(target):
    """Build the virtual forced-job entries and stage nation modifiers.

    Returns (disease_job_details, disease_jobs_assigned, disease_modifier_totals).
    """
    disease_job_details = {}
    disease_jobs_assigned = {}
    disease_modifier_totals = {}

    cache = target.get("_calc_cache") or {}
    counts = cache.get("disease_infection_counts")
    if counts is None:
        counts = get_nation_infection_counts(target.get("_id", ""))
    if not counts:
        return disease_job_details, disease_jobs_assigned, disease_modifier_totals

    pop_count = cache.get("pop_count", target.get("pop_count", 0)) or 0
    diseases = resolve_diseases(counts.keys())

    for disease_id, infected in counts.items():
        disease = diseases.get(disease_id)
        # Note: a discovered cure ("cured") does not free infected pops from
        # their forced job — it only doubles natural_cure_chance going
        # forward. Pops stay forced until they actually recover or the
        # nation is otherwise resolved (civil war / acceptance).
        if not disease or infected <= 0:
            continue

        stage_idx = active_stage_index(disease, infected, pop_count)
        stage = get_stage(disease, stage_idx)

        job_name = disease.get("job_type", "") or disease.get("name", "Diseased")
        production = kv_list_to_dict(disease.get("job_production"))
        upkeep = kv_list_to_dict(disease.get("job_upkeep"))
        stage_name = ""
        if stage:
            if stage.get("job_type_override"):
                job_name = stage["job_type_override"]
            stage_production = kv_list_to_dict(stage.get("job_production_override"))
            if stage_production:
                production = stage_production
            stage_upkeep = kv_list_to_dict(stage.get("job_upkeep_override"))
            if stage_upkeep:
                upkeep = stage_upkeep
            stage_name = stage.get("stage_name", "")
            for key, value in kv_list_to_dict(stage.get("nation_modifiers")).items():
                disease_modifier_totals[key] = disease_modifier_totals.get(key, 0) + value

        job_key = disease_job_key(disease)
        forced_count = min(infected, pop_count) if pop_count else infected
        disease_job_details[job_key] = {
            "display_name": job_name,
            "production": production,
            "upkeep": upkeep,
            "disease": True,
            "static": True,
            "forced_count": forced_count,
            "disease_name": disease.get("name", ""),
            "disease_id": disease_id,
            "stage_name": stage_name,
        }
        disease_jobs_assigned[job_key] = forced_count

    return disease_job_details, disease_jobs_assigned, disease_modifier_totals


# ---------------------------------------------------------------------------
# Derived races and per-pop infection mutation
# ---------------------------------------------------------------------------

def get_primary_race_name(nation):
    """The nation's primary race name, from _calc_cache when available."""
    cache = nation.get("_calc_cache") or {}
    if "primary_race_name" in cache:
        return cache.get("primary_race_name", "")
    try:
        race = mongo.db.races.find_one(
            {"_id": ObjectId(str(nation.get("primary_race", "")))}, {"name": 1})
        return (race or {}).get("name", "")
    except Exception:
        return ""


def nation_accepts_disease(nation, disease):
    """True when the nation has 'accepted' a race-changing disease: its primary
    race carries the disease's derived-race prefix (e.g. 'Vampiric Thumpkins'
    for Vampirism). Accepted nations are not treated as diseased — the derived
    race is their identity, and the disease's custom job unlocks for them."""
    if not disease.get("changes_race") or not disease.get("race_prefix"):
        return False
    return get_primary_race_name(nation).startswith(disease["race_prefix"] + " ")


def nation_accepts_disease_by_name(nation, disease_name):
    try:
        disease = mongo.db.diseases.find_one(
            {"name": disease_name},
            {"changes_race": 1, "race_prefix": 1})
    except Exception:
        return False
    if not disease:
        return False
    return nation_accepts_disease(nation, disease)


def get_or_create_derived_race(base_race_doc, prefix, positive_trait="", negative_trait=""):
    """Find or create the '<prefix> <Base Race>' derived race.

    Returns the derived race's id as a string, or "" when the base race is
    unusable (no name) or already carries the prefix.
    Generalized from the vampiric_races admin tool.
    """
    base_name = (base_race_doc or {}).get("name", "")
    prefix = (prefix or "").strip()
    if not base_name or not prefix or base_name.startswith(prefix + " "):
        return ""

    target_name = f"{prefix} {base_name}"
    derived = mongo.db.races.find_one({"name": target_name}, {"_id": 1})
    if derived:
        return str(derived["_id"])

    new_id = mongo.db.races.insert_one({
        "name": target_name,
        "founding_nation": "",
        "positive_trait": positive_trait or "",
        "negative_trait": negative_trait or "",
        "preferred_terrain": base_race_doc.get("preferred_terrain", "None"),
    }).inserted_id
    return str(new_id)


def infect_pop(pop_doc, disease):
    """Infect a single pop with a disease. Returns the $set update applied.

    For race-changing diseases the pop's race is swapped to the derived race
    and the original is stored in pre_disease_race for restoration on cure.
    """
    updates = {"disease": str(disease.get("_id", ""))}

    if disease.get("changes_race") and disease.get("race_prefix"):
        race_id = pop_doc.get("race", "")
        base_race = None
        if race_id:
            try:
                base_race = mongo.db.races.find_one({"_id": ObjectId(str(race_id))})
            except Exception:
                base_race = None
        derived_id = get_or_create_derived_race(
            base_race,
            disease.get("race_prefix", ""),
            disease.get("race_positive_trait", ""),
            disease.get("race_negative_trait", ""),
        )
        if derived_id:
            updates["pre_disease_race"] = str(race_id)
            updates["race"] = derived_id

    mongo.db.pops.update_one({"_id": pop_doc["_id"]}, {"$set": updates})
    return updates


def cure_pop(pop_doc):
    """Clear a pop's disease, restoring its pre-disease race when stored."""
    set_updates = {}
    unset_updates = {"disease": "", "pre_disease_race": ""}
    if pop_doc.get("pre_disease_race"):
        set_updates["race"] = pop_doc["pre_disease_race"]
    update = {"$unset": unset_updates}
    if set_updates:
        update["$set"] = set_updates
    mongo.db.pops.update_one({"_id": pop_doc["_id"]}, update)


def cure_disease_pops(disease_id):
    """Cure every pop infected with a disease. Returns {nation_id: cured_count}."""
    cured_by_nation = {}
    for pop in mongo.db.pops.find({"disease": str(disease_id)}):
        cure_pop(pop)
        nation_id = str(pop.get("nation", ""))
        cured_by_nation[nation_id] = cured_by_nation.get(nation_id, 0) + 1
    return cured_by_nation


def _derived_race_ids(disease):
    """Race ids of the disease's derived races ('<prefix> ...')."""
    prefix = (disease.get("race_prefix") or "").strip()
    if not disease.get("changes_race") or not prefix:
        return set()
    try:
        return {str(r["_id"]) for r in mongo.db.races.find(
            {"name": {"$regex": f"^{prefix} "}}, {"_id": 1})}
    except Exception:
        return set()


def infect_random_pops(nation_id, disease, count):
    """Infect up to `count` random uninfected, non-slave pops of a nation.

    Pops already carrying the disease's derived race (e.g. an existing vampire
    pop for Vampirism) are not valid infection targets.
    Returns the number of pops actually infected.
    """
    candidates = list(mongo.db.pops.find({
        "nation": str(nation_id),
        "slave": {"$ne": True},
        "$or": [{"disease": {"$exists": False}}, {"disease": {"$in": [None, ""]}}],
    }))
    derived_ids = _derived_race_ids(disease)
    if derived_ids:
        candidates = [p for p in candidates if str(p.get("race", "")) not in derived_ids]
    if not candidates:
        return 0
    random.shuffle(candidates)
    infected = 0
    for pop in candidates[:count]:
        infect_pop(pop, disease)
        infected += 1
    return infected


def convert_pop_to_accepted(pop_doc, disease):
    """Convert a pop of an ACCEPTED nation to the disease's derived race.

    Unlike infection this is a plain, permanent race change: no disease marker,
    no pre_disease_race — the pop has joined the nation's identity (e.g. become
    a vampire in a vampire nation), it is not sick.
    Returns True when a conversion happened.
    """
    if not disease.get("changes_race") or not disease.get("race_prefix"):
        return False
    race_id = pop_doc.get("race", "")
    base_race = None
    if race_id:
        try:
            base_race = mongo.db.races.find_one({"_id": ObjectId(str(race_id))})
        except Exception:
            base_race = None
    derived_id = get_or_create_derived_race(
        base_race,
        disease.get("race_prefix", ""),
        disease.get("race_positive_trait", ""),
        disease.get("race_negative_trait", ""),
    )
    if not derived_id:
        return False
    mongo.db.pops.update_one(
        {"_id": pop_doc["_id"]},
        {"$set": {"race": derived_id},
         "$unset": {"disease": "", "pre_disease_race": ""}},
    )
    return True


# ---------------------------------------------------------------------------
# Accepted-nation spread (voluntary vampirism etc.)
# ---------------------------------------------------------------------------

def convert_random_own_pop(nation, disease):
    """Convert one random non-derived, non-slave pop of an ACCEPTED nation to
    the disease's derived race. Returns the pop doc or None."""
    derived_ids = _derived_race_ids(disease)
    candidates = [
        p for p in mongo.db.pops.find({"nation": str(nation["_id"]), "slave": {"$ne": True}})
        if str(p.get("race", "")) not in derived_ids
    ]
    if not candidates:
        return None
    pop = random.choice(candidates)
    if convert_pop_to_accepted(pop, disease):
        return pop
    return None


def attempt_dual_spread(nation, disease, internal_fn):
    """Shared 50/50 internal-vs-external targeting used by both the outbreak
    spread tick and the accepted-nation spread tick, so the two mechanics stay
    identical.

    `internal_fn()` performs the internal spread action and returns True on
    success. On a coin flip, whichever target type is tried first that
    succeeds wins; if it fails (no valid victim), the other type is tried.

    Returns (succeeded, target_type, external_target_nation_or_None) where
    target_type is "internal", "external", or None if neither succeeded.
    """
    internal_first = random.random() < 0.5
    order = ("internal", "external") if internal_first else ("external", "internal")
    for target_type in order:
        if target_type == "internal":
            if internal_fn():
                return True, "internal", None
        else:
            target = pick_external_spread_target(nation, disease)
            if target is not None and infect_random_pops(str(target["_id"]), disease, 1):
                return True, "external", target
    return False, None, None


def pick_external_spread_target(nation, disease, max_distance=5):
    """Pick the outside nation an accepted nation's spread reaches.

    Eligible: nations with territory within `max_distance` hexes of the source
    nation's border that have not themselves accepted the disease. Nations with
    an ongoing trade route to the source, or whose cities are road-connected to
    it, are twice as likely to be picked.
    Returns a nation doc or None.
    """
    source_name = nation.get("name", "")
    if not source_name:
        return None

    try:
        from helpers.hex_map_helpers import get_nations_within_distance
        nearby = get_nations_within_distance(source_name, max_distance)
    except Exception:
        nearby = []
    if not nearby:
        return None

    connected = set()
    try:
        for route in mongo.db.trade_routes.find(
                {"status": {"$in": ["active", "ending"]},
                 "$or": [{"nation_a": source_name}, {"nation_b": source_name}]},
                {"nation_a": 1, "nation_b": 1}):
            other = route.get("nation_a") if route.get("nation_b") == source_name else route.get("nation_b")
            if other:
                connected.add(other)
    except Exception:
        pass
    try:
        from helpers.trade_route_helpers import get_connectable_nations
        connected |= {
            c.get("name") for c in get_connectable_nations(source_name, nation.get("trade_speed", 1))
            if c.get("name")
        }
    except Exception:
        pass

    candidates = []
    weights = []
    for cand_name in nearby:
        cand = mongo.db.nations.find_one({"name": cand_name})
        if not cand or nation_accepts_disease(cand, disease):
            continue
        candidates.append(cand)
        weights.append(2 if cand_name in connected else 1)
    if not candidates:
        return None
    return random.choices(candidates, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# Disease-driven civil war split
# ---------------------------------------------------------------------------

# Fields never copied onto a breakaway nation (mirrors the civil war helper's
# exclusions plus military/holdings, which stay with the source nation).
_DISEASE_SPLIT_EXCLUDED_FIELDS = [
    "_id", "name", "players", "breakdowns", "visibility_modifiers", "previous_names",
    "vassals", "overlord", "concessions", "progress_quests", "shared_quests",
    "disease_stages", "disease_spread_rolls", "_calc_cache",
    "land_units", "naval_units", "support_units",
    "districts", "cities", "wonders", "imperial_district",
]


def _unique_nation_name(base_name):
    name = base_name
    suffix = 2
    while mongo.db.nations.find_one({"name": name}, {"_id": 1}):
        name = f"{base_name} {suffix}"
        suffix += 1
    return name


def execute_disease_civil_war(source_nation, disease, infected_count):
    """Split a nation's infected pops into a breakaway nation.

    The breakaway copies the source (minus exclusions), takes a money/resource
    share proportional to its pop share, and its primary race becomes the
    disease's derived race (when configured). Both sides drop to Unsettled
    stability. Returns (new_nation_name, moved_pop_count) or (None, 0).
    """
    from copy import deepcopy
    from helpers.change_helpers import system_request_change, system_approve_change

    source_id = str(source_nation["_id"])
    pop_count = max(source_nation.get("pop_count", 0), 1)

    # A fully-infected nation has no healthy side to split from — splitting
    # would move every pop (and all resources) into the breakaway, leaving an
    # empty husk that immediately re-triggers. Callers should treat the nation
    # as having fully succumbed instead.
    if infected_count >= source_nation.get("pop_count", 0):
        return None, 0

    base_name = f"{disease.get('job_type', disease.get('name', 'Infected'))} {source_nation.get('name', '')}".strip()
    new_name = _unique_nation_name(base_name)

    new_nation = deepcopy(source_nation)
    for field in _DISEASE_SPLIT_EXCLUDED_FIELDS:
        new_nation.pop(field, None)
    new_nation["name"] = new_name
    new_nation["stability"] = "Unsettled"

    # Primary race: the disease's derived race when it changes races, based on
    # the source's primary race; otherwise keep the copied primary race.
    if disease.get("changes_race") and disease.get("race_prefix"):
        base_race = None
        try:
            base_race = mongo.db.races.find_one({"_id": ObjectId(str(source_nation.get("primary_race", "")))})
        except Exception:
            base_race = None
        derived_id = get_or_create_derived_race(
            base_race,
            disease.get("race_prefix", ""),
            disease.get("race_positive_trait", ""),
            disease.get("race_negative_trait", ""),
        )
        if derived_id:
            new_nation["primary_race"] = derived_id

    # Money / resource share proportional to the infected pop share.
    share = min(max(infected_count / pop_count, 0.0), 1.0)
    money_moved = int(source_nation.get("money", 0) * share)
    storage_moved = {}
    for res, qty in (source_nation.get("resource_storage") or {}).items():
        moved = int((qty or 0) * share)
        if moved:
            storage_moved[res] = moved
    new_nation["money"] = money_moved
    new_nation["resource_storage"] = storage_moved

    change_id = system_request_change(
        data_type="nations",
        item_id=None,
        change_type="Add",
        before_data={},
        after_data=new_nation,
        reason=f"Disease civil war: {disease.get('name', 'disease')} split from {source_nation.get('name', '')}",
    )
    if change_id is None:
        return None, 0
    system_approve_change(change_id)

    new_doc = mongo.db.nations.find_one({"name": new_name}, {"_id": 1})
    if new_doc is None:
        return None, 0
    new_id = str(new_doc["_id"])

    # Subtract the transferred money/resources from the source.
    if money_moved or storage_moved:
        updates = {"money": source_nation.get("money", 0) - money_moved}
        for res, moved in storage_moved.items():
            updates[f"resource_storage.{res}"] = (source_nation.get("resource_storage", {}).get(res, 0) or 0) - moved
        mongo.db.nations.update_one({"_id": source_nation["_id"]}, {"$set": updates})

    # Move the infected pops to the breakaway nation.
    result = mongo.db.pops.update_many(
        {"nation": source_id, "disease": str(disease.get("_id", ""))},
        {"$set": {"nation": new_id}},
    )

    # For race-changing diseases the breakaway is an ACCEPTED nation (its
    # primary race is the derived race): its pops are not sick, they simply
    # ARE the derived race now. Clear the disease markers — they keep their
    # derived races permanently and gain the disease's custom job instead.
    if disease.get("changes_race") and disease.get("race_prefix"):
        mongo.db.pops.update_many(
            {"nation": new_id, "disease": str(disease.get("_id", ""))},
            {"$unset": {"disease": "", "pre_disease_race": ""}},
        )

    return new_name, result.modified_count
