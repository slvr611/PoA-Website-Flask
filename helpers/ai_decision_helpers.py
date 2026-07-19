"""
AI Decision System for NPC nations.

Entry point: ai_decision_tick(old_nation, new_nation, schema)
  - Evaluates resource situation from old_nation (post-calculate_all_fields)
  - Assigns jobs on new_nation (runs after nation_job_cleanup_tick)
  - Plans / executes district construction on new_nation
  - Generates informed resource_desires replacing the old random system
  - Stores goals + decision log in new_nation["ai_state"] for player visibility

Mid-session matching: ai_market_matching_tick(old_nations, new_nations, schema)
  - Runs as a NATION_CROSS_TICK_FUNCTION partway through the session
  - Auto-matches unfilled AI buy/sell orders within the same market
"""

import math
from copy import deepcopy
from bson import ObjectId

from app_core import mongo, json_data, category_data

# Resources broadly useful for almost any goal or district — the AI keeps a
# higher baseline urgency for these even when self-sustaining, since they're
# rarely truly "surplus" the way a specialty resource like magic might be.
ALWAYS_USEFUL_RESOURCES = {"food", "wood", "stone"}

# ---------------------------------------------------------------------------
# Per-tick district_defs cache
# Loaded once at the start of each AI tick and cleared afterward so that
# score_buildable_districts (called up to 6× per nation × 180 nations) never
# issues 1,000+ identical DB queries in one session.
# ---------------------------------------------------------------------------
_DISTRICT_DEFS_CACHE = None

def _load_district_defs_cache():
    """Load district_defs into the module-level cache. Call once per tick."""
    global _DISTRICT_DEFS_CACHE
    try:
        _DISTRICT_DEFS_CACHE = list(mongo.db.district_defs.find({}, {"_id": 0}))
    except Exception:
        _DISTRICT_DEFS_CACHE = []

def _clear_district_defs_cache():
    """Release the cached district_defs list after the tick completes."""
    global _DISTRICT_DEFS_CACHE
    _DISTRICT_DEFS_CACHE = None

# ---------------------------------------------------------------------------
# Personality — how racial traits and temperament bias the AI
# ---------------------------------------------------------------------------

# Additive deltas applied to ai_personality from racial traits
TRAIT_PERSONALITY_BIAS = {
    # positive traits
    "Aggressive":   {"aggression": 0.3,  "military": 0.2},
    "Aquatic":      {"expansion": 0.15},
    "Charismatic":  {"trade": 0.4},
    "Curious":      {"economic": 0.25},
    "Docile":       {"aggression": -0.3},
    "Fecund":       {"expansion": 0.2},
    "Industrious":  {"economic": 0.4},
    "Semi-Aquatic": {"expansion": 0.15},
    "Sturdy":       {"military": 0.15},
    "Swift":        {"military": 0.1,   "expansion": 0.1},
    # negative traits
    "Bloodthirsty": {"aggression": 0.4,  "military": 0.3},
    "Insatiable":   {"economic": 0.2},
    "Irksome":      {"trade": -0.3},
    "Rowdy":        {"aggression": 0.2},
}

# Multipliers applied per temperament dimension after trait biases
TEMPERAMENT_BIAS_MULT = {
    "Aggressive":  {"aggression": 1.5,  "military": 1.5},
    "Supremacist": {"aggression": 1.3,  "expansion": 1.3, "military": 1.2},
    "Curious":     {"economic": 1.5},
    "Welcoming":   {"aggression": 0.4,  "trade": 1.3},
    "Withdrawn":   {"aggression": 0.2,  "trade": 0.7,     "expansion": 0.4},
    "Zealous":     {"military": 1.3,    "aggression": 1.2},
    "Neutral":     {},
}

# Special production fields → (stockpile key used for need-weight lookup, base weight)
PRODUCTION_FIELD_MAP = {
    "money_income":             ("money",     1.0),
    "stability_gain_chance":    (None,        0.6),   # constant modest value
    "stability_loss_chance":    (None,       -0.8),
    "nation_magic_spell_rolls": (None,        0.3),
    "rerolls":                  (None,        0.4),
    "administration":           (None,        0.4),
    "ruler_artifact_slots":     (None,        0.25),
    "backlash_reduction":       (None,        0.3),
}

# Fields where a NEGATIVE modifier value is beneficial (e.g. guardhouse's
# stability_loss_chance -0.35). Goal-alignment matching must accept these.
NEGATIVE_IS_GOOD = frozenset({
    "stability_loss_chance",
    "blacklash_chance",
    "stability_loss_chance_on_leader_death",
    "tech_category_cost_modifier",
    "technology_cost_modifier",
    "defensive_visibility",
})

# ---------------------------------------------------------------------------
# Strategic goals — the goal-first AI architecture
# ---------------------------------------------------------------------------

STRATEGIC_GOALS = {
    "stabilize_economy": {
        "display_name": "Stabilize Economy",
        "description": "Fix resource deficits, build a more efficient base economy",
        "base_urgency": 0,
        "personality_dims": {},
    },
    "stabilize_nation": {
        "display_name": "Stabilize Nation",
        "description": "Raise stability, reduce instability — even at cost of economy",
        "base_urgency": 0,
        "personality_dims": {},
    },
    "grow_economy": {
        "display_name": "Grow Economy",
        "description": "Build economic infrastructure, open new districts, improve efficiency",
        "base_urgency": 40,
        "personality_dims": {"economic": 25, "trade": 10},
    },
    "prepare_war": {
        "display_name": "Prepare for War",
        "description": "Build military strength, stockpile strategic resources",
        "base_urgency": 25,
        "personality_dims": {"aggression": 30, "military": 25},
    },
    "develop_technology": {
        "display_name": "Develop Technology",
        "description": "Research new technologies",
        "base_urgency": 15,
        "personality_dims": {"economic": 10},
    },
    "grow_population": {
        "display_name": "Grow Population",
        "description": "Build cities to expand pop capacity",
        "base_urgency": 20,
        "personality_dims": {"expansion": 25},
    },
    "convert_population": {
        "display_name": "Convert Population",
        "description": "Build churches/homes for religious and cultural conversion",
        "base_urgency": 15,
        "personality_dims": {"aggression": 10, "expansion": 10},
    },
    "expand_trade": {
        "display_name": "Expand Trade",
        "description": "Sell surplus resources, maximize trade income",
        "base_urgency": 25,
        "personality_dims": {"trade": 30, "economic": 10},
    },
    "build_wonder": {
        "display_name": "Build Wonder",
        "description": "Construct a wonder when the nation is comfortable",
        "base_urgency": 10,
        "personality_dims": {"economic": 5},
    },
}

GOAL_TEMPERAMENT_MULT = {
    "Aggressive":  {"prepare_war": 1.3, "stabilize_economy": 0.8, "stabilize_nation": 0.8},
    "Supremacist": {"prepare_war": 1.2, "convert_population": 1.2, "grow_population": 1.1},
    "Curious":     {"develop_technology": 1.4, "grow_economy": 1.2},
    "Welcoming":   {"expand_trade": 1.3, "prepare_war": 0.6, "convert_population": 0.7},
    "Withdrawn":   {"expand_trade": 0.5, "prepare_war": 0.7, "stabilize_economy": 1.2, "stabilize_nation": 1.2},
    "Zealous":     {"convert_population": 1.3, "prepare_war": 1.2},
    "Neutral":     {},
}

# Real district_defs categories: production, food, commerce, warfare, society,
# government, education, magic. Any other name here silently never matches.
GOAL_DISTRICT_AFFINITY = {
    "stabilize_economy": {
        "job_resources": [],
        "categories": [],
        "bonus": 1.5,
        "dynamic": True,
    },
    "stabilize_nation": {
        "job_resources": ["stability_gain_chance", "stability_loss_chance", "max_stability_gain_chance"],
        "modifier_fields": ["stability_gain_chance", "stability_loss_chance", "max_stability_gain_chance"],
        "categories": [],
        "bonus": 1.5,
    },
    "grow_economy": {
        "job_resources": ["food", "wood", "stone", "iron", "mounts", "money_income"],
        "categories": ["production", "food", "commerce"],
        "bonus": 1.3,
    },
    "prepare_war": {
        "job_resources": ["iron", "gunpowder"],
        "categories": ["warfare"],
        "bonus": 1.4,
    },
    "develop_technology": {
        "job_resources": ["research"],
        "modifier_fields": ["research"],
        "categories": [],
        "bonus": 1.5,
    },
    "grow_population": {
        "modifier_fields": ["effective_pop_capacity"],
        "categories": [],
        "bonus": 1.4,
    },
    "convert_population": {
        "job_resources": ["backlash_reduction"],
        "def_keys": ["church", "home"],
        "categories": [],
        "bonus": 1.4,
    },
    "expand_trade": {
        "modifier_fields": ["trade_slots", "money_income", "trade_speed", "route_capacity"],
        "categories": ["commerce"],
        "bonus": 1.3,
    },
    "build_wonder": {
        "categories": ["wonder"],
        "bonus": 1.5,
    },
}


# ---------------------------------------------------------------------------
# Resource helpers
# ---------------------------------------------------------------------------

def _general_and_unique_keys():
    return (
        [r["key"] for r in json_data.get("general_resources", [])] +
        [r["key"] for r in json_data.get("unique_resources", [])]
    )

def _luxury_keys():
    return [r["key"] for r in json_data.get("luxury_resources", [])]

def _base_prices():
    prices = {}
    for r in (
        json_data.get("general_resources", []) +
        json_data.get("unique_resources", []) +
        json_data.get("luxury_resources", [])
    ):
        prices[r["key"]] = r.get("base_price", 10)
    return prices


# ---------------------------------------------------------------------------
# Personality
# ---------------------------------------------------------------------------

def get_ai_personality(nation):
    """Return effective personality dict blending stored values + racial traits + temperament."""
    dims = ("aggression", "economic", "expansion", "trade", "military")
    personality = {d: float(nation.get("ai_personality", {}).get(d, 0.0)) for d in dims}

    race_id = nation.get("primary_race", "")
    if race_id:
        try:
            race = mongo.db.races.find_one(
                {"_id": ObjectId(race_id)},
                {"positive_trait": 1, "negative_trait": 1}
            )
            if race:
                for trait in (race.get("positive_trait", ""), race.get("negative_trait", "")):
                    for dim, delta in TRAIT_PERSONALITY_BIAS.get(trait or "", {}).items():
                        personality[dim] = max(-1.0, min(1.0, personality[dim] + delta))
        except Exception:
            pass

    for dim, mult in TEMPERAMENT_BIAS_MULT.get(nation.get("temperament", "Neutral"), {}).items():
        personality[dim] = max(-1.0, min(1.0, personality[dim] * mult))

    return personality


# ---------------------------------------------------------------------------
# Nation state snapshot
# ---------------------------------------------------------------------------

def evaluate_nation_state(old_nation):
    """
    Build a snapshot of the nation's resource situation and capacity.
    Uses old_nation which has calculate_all_fields already applied.
    resource_excess = net resource change per tick (from nation_income_tick).
    """
    common = _general_and_unique_keys()
    luxury = _luxury_keys()
    all_res = common + luxury

    # Strip last session's (now-cleared) job effects to get a structural baseline,
    # so initial weights reflect what the nation produces with zero jobs assigned.
    #
    # Production and consumption are stripped SEPARATELY and re-clamped at 0,
    # mirroring compute_resource_production / compute_resource_consumption.
    # Working on the combined excess would lose the production floor: a nation
    # with a global production malus (e.g. Savior's -1 to everything) absorbed
    # by the clamp would show a phantom deficit after job production is removed
    # (hunter 2 food, savior -1 → production 1; strip 2 → -1, but the true
    # no-jobs production is max(0-1, 0) = 0) — and the AI would pin a pop to
    # "fix" a deficit that doesn't exist without the job.
    persistent_job_keys = {"revolutionary"}
    base_production  = dict(old_nation.get("resource_production", {}))
    base_consumption = dict(old_nation.get("resource_consumption", {}))
    base_excess = dict(old_nation.get("resource_excess", {}))
    # Strip using the calculated per-nation job_details when available — the
    # game applies job production/upkeep WITH modifiers (e.g. a sawmill's
    # harvester wood bonus), so the strip must remove the same boosted amounts.
    calculated_job_details = old_nation.get("job_details") or {}
    raw_jobs_map = json_data.get("jobs", {})
    for jk, count in old_nation.get("jobs", {}).items():
        if count <= 0 or jk in persistent_job_keys:
            continue
        jdata = calculated_job_details.get(jk) or raw_jobs_map.get(jk, {})
        for r, amt in jdata.get("production", {}).items():
            if not isinstance(amt, (int, float)):
                continue
            if r in base_production:
                base_production[r] = base_production.get(r, 0) - amt * count
            else:
                # Non-resource production fields (stability chances etc.)
                base_excess[r] = base_excess.get(r, 0) - amt * count
        for r, amt in jdata.get("upkeep", {}).items():
            if not isinstance(amt, (int, float)):
                continue
            if r in base_consumption:
                base_consumption[r] = base_consumption.get(r, 0) - amt * count
            else:
                base_excess[r] = base_excess.get(r, 0) + amt * count
    # Recompute excess for resource keys with both sides clamped at 0, exactly
    # as the game does when no jobs are assigned.
    for r in set(base_production) | set(base_consumption):
        base_excess[r] = int(max(base_production.get(r, 0), 0) - max(base_consumption.get(r, 0), 0))
    resource_excess  = base_excess

    # How much production malus the clamp is absorbing per resource (a negative
    # stripped baseline means e.g. Savior's -1 is sitting under the 0 floor).
    # The first units of NEW production must overcome this before they raise
    # net production — job projection loops consume it before crediting.
    production_clamp_absorbed = {
        r: -v for r, v in base_production.items()
        if isinstance(v, (int, float)) and v < 0
    }
    resource_storage = old_nation.get("resource_storage", {})

    stockpiles = {}
    net_production = {}
    sessions_until_empty = {}

    for r in all_res:
        stockpile = resource_storage.get(r, 0)
        net       = resource_excess.get(r, 0)
        stockpiles[r]      = stockpile
        net_production[r]  = net
        if net < 0 and stockpile > 0:
            sessions_until_empty[r] = stockpile / (-net)
        elif net < 0:
            sessions_until_empty[r] = 0.0
        else:
            sessions_until_empty[r] = float("inf")

    # Money tracked separately
    money        = old_nation.get("money", 0)
    money_income = old_nation.get("money_income", 0)

    # Available jobs — reuse existing requirement checker.
    # Resolve region_name/def_keys once instead of letting check_job_requirements
    # re-query the region for every single job (was a real N+1 query hotspot —
    # ~1 second per nation just from this loop with the region DB round-trip
    # repeated per job).
    from calculations.field_calculations import check_job_requirements
    from bson import ObjectId as _ObjectId
    region_name = ""
    region_id = old_nation.get("region", "")
    if region_id:
        try:
            region_doc = category_data["regions"]["database"].find_one(
                {"_id": _ObjectId(region_id)}, {"name": 1}
            )
            region_name = region_doc["name"] if region_doc else ""
        except Exception:
            region_name = ""
    def_keys = [d.get("def_key", "") for d in old_nation.get("districts", []) if d.get("def_key")]

    # Persistent jobs (revolution) are granted by other game mechanics, not
    # chosen by the player/AI — exclude them from available_jobs entirely so
    # the AI never assigns idle pops to them. (Disease jobs are virtual and
    # never appear in jobs.json; infected pops are excluded via the infection
    # counts added to persistent_assigned below.)
    persistent = {"revolutionary"}

    # Prefer the calculated per-nation job_details: they carry the boosted
    # production/upkeep from districts/laws/techs (e.g. sawmill's harvester
    # wood bonus) and are already filtered by requirements and locks. Without
    # them the AI scores jobs on raw base values and can pass on a job the
    # nation just built a district to improve.
    available_jobs = {}
    if calculated_job_details:
        for jk, jdata in calculated_job_details.items():
            if jk not in persistent:
                available_jobs[jk] = jdata
    else:
        jobs_data = json_data.get("jobs", {})
        for jk, jdata in jobs_data.items():
            if jk in persistent:
                continue
            if old_nation.get(f"locks_{jk}", 0):
                continue
            if check_job_requirements(old_nation, jdata, {}, region_name=region_name, def_keys=def_keys):
                available_jobs[jk] = jdata

    # Persistent jobs that survive cleanup and shouldn't be re-assigned
    persistent_assigned = sum(
        old_nation.get("jobs", {}).get(j, 0) for j in persistent
    )
    # Infected pops are force-assigned to virtual disease jobs (per-pop
    # disease field, never stored in nation["jobs"]) — treat them as
    # already-assigned so the AI doesn't double-book them.
    try:
        from helpers.disease_helpers import get_nation_infection_counts
        persistent_assigned += sum(
            get_nation_infection_counts(str(old_nation.get("_id", ""))).values()
        )
    except Exception:
        pass
    total_pops = old_nation.get("pop_count", 0)
    idle_pops  = max(0, total_pops - persistent_assigned)

    # District inventory
    existing_def_keys = set()
    existing_def_key_counts = {}  # {def_key: count} for map_count-aware checks
    existing_types    = set()
    for d in old_nation.get("districts", []):
        if d.get("def_key"):
            dk = d["def_key"]
            existing_def_keys.add(dk)
            existing_def_key_counts[dk] = existing_def_key_counts.get(dk, 0) + 1
        if d.get("type"):
            existing_types.add(d["type"])

    district_slots       = old_nation.get("district_slots", 0)
    current_district_cnt = sum(
        1 for d in old_nation.get("districts", [])
        if isinstance(d, dict) and (d.get("def_key") or d.get("type"))
    )
    open_district_slots  = max(0, district_slots - current_district_cnt)

    # District cost modifier (e.g. -0.3 for Industrious race)
    district_cost_mod = old_nation.get("district_cost", 0)

    territory_types = old_nation.get("territory_types", {})

    # Resources the nation actually participates in — produced or consumed by
    # any source (districts, terrain, jobs, modifiers). Used by _weights_from_net
    # so untouched exotics (zero stock, zero use) don't get the low-stockpile
    # buffer weight and outrank staples the nation actually runs on.
    active_resources = set()
    for src in (old_nation.get("resource_production", {}), old_nation.get("resource_consumption", {})):
        for r, amt in src.items():
            if isinstance(amt, (int, float)) and amt > 0:
                active_resources.add(r)
    for r, net in net_production.items():
        if net != 0:
            active_resources.add(r)

    # Headroom before job production of stability_gain_chance / stability_loss_chance
    # stops having any real effect. stability_gain_chance is clamped at
    # stability_gain_chance_cap (jobs push it UP toward the cap); stability_loss_chance
    # is clamped at a 0 floor (jobs like guard push it DOWN via negative production).
    # Only resources/fields with genuinely limited headroom are included — a field
    # missing from this dict is treated as unlimited by _consume_headroom/score_jobs.
    stability_chance_headroom = {}
    gain_cap = old_nation.get("stability_gain_chance_cap", 1.0)
    gain_now = old_nation.get("stability_gain_chance", 0.0)
    stability_chance_headroom["stability_gain_chance"] = max(0.0, gain_cap - gain_now)
    loss_now = old_nation.get("stability_loss_chance", 0.0)
    stability_chance_headroom["stability_loss_chance"] = max(0.0, loss_now)

    return {
        "stockpiles":            stockpiles,
        "net_production":        net_production,
        "sessions_until_empty":  sessions_until_empty,
        "money":                 money,
        "money_income":          money_income,
        "available_jobs":        available_jobs,
        "idle_pops":             idle_pops,
        "total_pops":            total_pops,
        "trade_slots":           old_nation.get("trade_slots", 0),
        "remaining_import_slots": old_nation.get("remaining_import_slots", 0),
        "remaining_export_slots": old_nation.get("remaining_export_slots", 0),
        "existing_def_keys":     existing_def_keys,
        "existing_def_key_counts": existing_def_key_counts,
        "existing_types":        existing_types,
        "open_district_slots":   open_district_slots,
        "district_cost_mod":     district_cost_mod,
        "trade_speed":           old_nation.get("trade_speed", 7),
        "territory_types":       territory_types,
        "resource_capacity":     old_nation.get("nation_resource_capacity", {}),
        "active_resources":      active_resources,
        "production_clamp_absorbed": production_clamp_absorbed,
        "stability_chance_headroom": stability_chance_headroom,
    }


# ---------------------------------------------------------------------------
# Need weights
# ---------------------------------------------------------------------------

def _weights_from_net(net_production, stockpiles, prices, money_income, resource_capacity=None, active_resources=None, money_stock=None):
    """
    Core weight computation from a net-production snapshot.
    Called by compute_need_weights and by assign_ai_jobs after each pop assignment.

    `prices` should be dynamic market prices (from get_stored_market_prices) or
    static base prices as a fallback.  Surplus floor is a flat 0.3 — price
    normalization is NOT baked in here since every caller already multiplies
    the returned weight by _price_scale(resource, prices) downstream. Baking
    price into both the weight and the scale would double-count it, unfairly
    penalizing upkeep/cost in resources priced above the 50 baseline (and
    unfairly favoring production of the same).

    When net production is positive (or zero) the resource is self-sustaining —
    weight drops to the surplus/buffer tier even if the current stockpile is low.
    This prevents the greedy job loop from over-assigning to a single deficit
    after enough pops have already been placed to fix it.

    If resource_capacity is provided, resources at or near their storage cap
    get heavily reduced weight to discourage wasting production on overflow.
    """
    weights = {}
    for r, net in net_production.items():
        stockpile = stockpiles.get(r, 0)

        if net >= 0:
            # The low-stockpile buffer weight only applies to resources the
            # nation actually participates in (when active_resources is known):
            # an exotic it neither produces nor consumes is not worth 1.3/unit
            # just because the stockpile is empty.
            participates = (
                active_resources is None
                or r in active_resources
                or r in ALWAYS_USEFUL_RESOURCES
            )
            if stockpile < 5 and net < 1 and participates:
                w = 1.3
            elif r in ALWAYS_USEFUL_RESOURCES:
                # Food, wood, and stone are broadly useful for almost any goal
                # or district — keep a meaningfully higher floor than other
                # surplus resources so the AI keeps stockpiling them by default,
                # unless a specific district need overrides via the cost boost.
                w = 0.8
            else:
                w = 0.3
        else:
            if stockpile > 0:
                sessions = stockpile / (-net)
            else:
                sessions = 0.0

            # Use the same floor as the surplus branch so weight transitions
            # smoothly across the net=0 boundary — a comfortably-buffered deficit
            # (e.g. 9 sessions) shouldn't score meaningfully higher than a
            # barely-positive resource of the same kind.
            floor_w = 0.8 if r in ALWAYS_USEFUL_RESOURCES else 0.3

            if sessions < 2:
                w = 5.0
            elif sessions < 4:
                w = 3.0
            else:
                w = floor_w + (2.0 - floor_w) * (4.0 / sessions)

        # Reduce weight for resources overflowing stockpile cap.
        # This penalty is recalculated dynamically during job assignment loops
        # as each assigned job's upkeep reduces projected net production.
        if resource_capacity and net > 0:
            cap = resource_capacity.get(r, 0)
            projected = stockpile + net
            if cap > 0 and projected >= cap:
                w *= 0.2
            elif cap > 0 and projected >= cap * 0.8:
                w *= 0.5

        weights[r] = w

    # Money urgency mirrors the resource tiers: a negative income with the
    # treasury about to run dry is as critical as a resource hitting zero.
    if money_income >= 0:
        weights["money"] = 1.2
    elif money_stock is not None:
        money_sessions = money_stock / -money_income if money_stock > 0 else 0.0
        if money_sessions < 2:
            weights["money"] = 6.0
        elif money_sessions < 4:
            weights["money"] = 4.0
        else:
            weights["money"] = 2.5
    else:
        weights["money"] = 2.5
    return weights


def compute_need_weights(state, prices=None):
    """
    Urgency weight per resource. Higher = more valuable to produce right now.
    Pass dynamic market prices as `prices` for demand-aware weights;
    falls back to static base prices when omitted.
    """
    return _weights_from_net(
        state["net_production"],
        state["stockpiles"],
        prices if prices is not None else _base_prices(),
        state["money_income"],
        state.get("resource_capacity"),
        active_resources=state.get("active_resources"), money_stock=state.get("money"),
    )


# ---------------------------------------------------------------------------
# Job scoring
# ---------------------------------------------------------------------------

def _consume_clamp_absorbed(absorbed, r, amount):
    """Consume the production-clamp-absorbed malus for resource r and return
    the EFFECTIVE amount that actually raises net production. Mutates
    `absorbed` — each unit of new production first fills the hole under the
    0 floor (e.g. Savior's -1) before having any real effect."""
    if not absorbed or amount <= 0:
        return amount
    eat = min(absorbed.get(r, 0), amount)
    if eat > 0:
        absorbed[r] -= eat
        if absorbed[r] <= 0:
            del absorbed[r]
        return amount - eat
    return amount


def _consume_headroom(headroom, field, amount):
    """Cap `amount` (a job's stability_gain_chance/stability_loss_chance
    production, which can be positive or negative) at the remaining headroom
    for `field` and decrement headroom by however much was actually used.
    Mutates `headroom`. Returns the effective (possibly reduced-magnitude)
    amount, sign preserved.

    stability_gain_chance headroom = distance up to stability_gain_chance_cap
    (jobs push the value UP; once at the cap, more gain-chance production is
    wasted). stability_loss_chance headroom = distance down to the 0 floor
    (jobs like guard push the value DOWN via negative production; once at 0,
    more reduction is wasted). Fields with no tracked headroom (not in the
    dict at all) are treated as unlimited and pass through unchanged — but a
    field that HAS reached exactly 0 headroom stays in the dict at 0 rather
    than being deleted, so it keeps clipping to nothing instead of reverting
    to "unlimited" once fully exhausted.
    """
    if not headroom or field not in headroom or amount == 0:
        return amount
    available = headroom[field]
    used = min(available, abs(amount))
    headroom[field] = available - used
    return used if amount > 0 else -used


def score_jobs(state, need_weights, prices=None, clamp_absorbed=None, stability_headroom=None):
    """
    Compute efficiency score for each available job.
    Factors in: resource need weights, market price normalisation, district bonus.

    clamp_absorbed: optional {resource: amount} of production malus currently
    absorbed by the per-resource clamp at 0. The NEXT pop's production of such
    a resource is reduced by that amount (read-only here — callers consume it
    when they actually assign the pop).

    stability_headroom: optional {field: amount} of remaining room before
    stability_gain_chance/stability_loss_chance production stops having any
    real effect (gain is capped from above at stability_gain_chance_cap; loss
    is capped from below at 0). Read-only here, same convention as
    clamp_absorbed — callers consume it via _consume_headroom when a pop is
    actually assigned. Without this, a nation already at its stability cap
    keeps scoring brewers/guards as if every unit of production still helps.
    """
    prices = prices if prices is not None else _base_prices()
    nation_districts = state["existing_def_keys"] | state["existing_types"]
    scores = {}

    for jk, job in state["available_jobs"].items():
        prod_value  = 0.0
        upkeep_cost = 0.0

        for field, amount in job.get("production", {}).items():
            if clamp_absorbed and isinstance(amount, (int, float)) and clamp_absorbed.get(field, 0) > 0:
                amount = max(0, amount - clamp_absorbed[field])
            if stability_headroom and isinstance(amount, (int, float)) and field in stability_headroom:
                amount = _consume_headroom(dict(stability_headroom), field, amount)
            if field in need_weights:
                prod_value += need_weights[field] * amount * _price_scale(field, prices)
            elif field in PRODUCTION_FIELD_MAP:
                stockpile_key, base_w = PRODUCTION_FIELD_MAP[field]
                scale = _price_scale(stockpile_key, prices) if stockpile_key else 1.0
                if stockpile_key and stockpile_key in need_weights:
                    prod_value += need_weights[stockpile_key] * amount * scale
                else:
                    prod_value += base_w * amount * scale

        for field, amount in job.get("upkeep", {}).items():
            w = need_weights.get(field, 1.0)
            upkeep_cost += w * amount * _price_scale(field, prices)

        base = prod_value - upkeep_cost

        district_bonus = 1.0
        req_districts  = job.get("requirements", {}).get("district", [])
        if req_districts and any(r in nation_districts for r in req_districts):
            primary_weight = 0.0
            best_prod_val  = 0.0
            for field, amount in job.get("production", {}).items():
                if stability_headroom and isinstance(amount, (int, float)) and field in stability_headroom:
                    amount = _consume_headroom(dict(stability_headroom), field, amount)
                if field in need_weights and isinstance(amount, (int, float)) and amount > 0:
                    pv = need_weights[field] * amount * _price_scale(field, prices)
                    if pv > best_prod_val:
                        best_prod_val  = pv
                        primary_weight = need_weights[field]
            district_bonus = 1.0 + 0.4 * min(1.0, primary_weight / 5.0)

        scores[jk] = max(0.0, base * district_bonus)

    return scores


# ---------------------------------------------------------------------------
# Job assignment
# ---------------------------------------------------------------------------

def assign_ai_jobs(state, need_weights, prices=None):
    """
    Greedily assign pops to jobs with dynamic weight recalculation after each assignment.
    After each pop is placed the projected net production updates, so the next pop's scores
    reflect the current resource situation rather than static initial weights.
    Returns ({job_key: count}, [log_strings]).
    """
    if state["idle_pops"] <= 0:
        return {}, []

    prices           = prices if prices is not None else _base_prices()
    projected_net    = dict(state["net_production"])
    assignments      = {}
    pops_remaining   = state["idle_pops"]
    nation_districts = state["existing_def_keys"] | state["existing_types"]

    while pops_remaining > 0:
        curr_weights = _weights_from_net(
            projected_net, state["stockpiles"], prices, state["money_income"],
            active_resources=state.get("active_resources"), money_stock=state.get("money"),
        )
        job_scores = score_jobs(state, curr_weights, prices)
        positive   = {k: v for k, v in job_scores.items() if v > 0.05}
        if not positive:
            break

        best = max(positive, key=lambda k: positive[k])
        assignments[best]  = assignments.get(best, 0) + 1
        pops_remaining    -= 1

        # Update projected net so the next pop sees the updated resource situation
        job = state["available_jobs"][best]
        for r, amt in job.get("production", {}).items():
            if isinstance(amt, (int, float)) and r in projected_net:
                projected_net[r] = projected_net.get(r, 0) + amt
        for r, amt in job.get("upkeep", {}).items():
            if isinstance(amt, (int, float)) and r in projected_net:
                projected_net[r] = projected_net.get(r, 0) - amt

    # Build log using initial weights for reference scores
    initial_scores = score_jobs(state, need_weights, prices)
    log = []
    for jk, cnt in sorted(assignments.items(), key=lambda x: -x[1]):
        if cnt == 0:
            continue
        job        = state["available_jobs"][jk]
        score      = initial_scores.get(jk, 0)
        req        = job.get("requirements", {}).get("district", [])
        bonus_note = " (+district bonus)" if req and any(r in nation_districts for r in req) else ""
        log.append(f"Assigned {cnt}× {job.get('display_name', jk)} (score {score:.1f}){bonus_note}")

    return {k: v for k, v in assignments.items() if v > 0}, log


# ---------------------------------------------------------------------------
# Upkeep floor — minimum jobs to sustain the nation
# ---------------------------------------------------------------------------

def compute_upkeep_floor(state, prices=None):
    """
    Assign the minimum workers needed so no resource is critically depleted.
    No pop cap — if the nation needs all pops for upkeep, it uses them all.

    Returns (upkeep_assignments, remaining_pops, projected_net, upkeep_log, upkeep_ratio, unresolved_deficits).
    upkeep_ratio = fraction of idle pops consumed by upkeep (0.0 – 1.0).
    unresolved_deficits = set of resources still in critical deficit because
        upkeep ran out of pops or no job could fix them.
    """
    if state["idle_pops"] <= 0:
        # No workforce at all — every critical deficit is unfixable by jobs.
        unresolved = set()
        for r, net in state["net_production"].items():
            if net < 0:
                stockpile = state["stockpiles"].get(r, 0)
                sessions = stockpile / (-net) if stockpile > 0 else 0
                if sessions < 4:
                    unresolved.add(r)
        state["unresolved_deficits"] = unresolved
        state["upkeep_burden"] = {}
        state["production_clamp_absorbed_remaining"] = dict(state.get("production_clamp_absorbed") or {})
        state["stability_chance_headroom_remaining"] = dict(state.get("stability_chance_headroom") or {})
        return {}, 0, dict(state["net_production"]), [], 0.0, unresolved

    prices = prices if prices is not None else _base_prices()
    projected_net = dict(state["net_production"])
    assignments = {}
    pops_remaining = state["idle_pops"]
    total_idle = state["idle_pops"]
    log = []

    # Production malus absorbed by the 0-floor clamp (e.g. Savior's -1): the
    # first units of newly assigned job production fill this hole before
    # raising net production. Scoring stays optimistic (so a job that needs a
    # second pop to overcome the malus still gets tried) but the projection is
    # exact — the loop keeps assigning until the deficit is REALLY fixed.
    clamp_absorbed = dict(state.get("production_clamp_absorbed") or {})

    # Headroom before stability_gain_chance/stability_loss_chance production
    # stops mattering (cap/floor already reached) — see evaluate_nation_state.
    # Upkeep jobs are only ever picked for producing a resource in deficit, so
    # this rarely changes which job wins here, but it keeps scores accurate
    # for jobs that also happen to touch stability as a side effect.
    stability_headroom = dict(state.get("stability_chance_headroom") or {})

    while pops_remaining > 0:
        # Find the most critical deficit
        worst_resource = None
        worst_sessions = float("inf")
        for r, net in projected_net.items():
            if net >= 0:
                continue
            stockpile = state["stockpiles"].get(r, 0)
            sessions = stockpile / (-net) if net < 0 and stockpile > 0 else 0.0
            if sessions < worst_sessions:
                worst_sessions = sessions
                worst_resource = r

        if worst_resource is None or worst_sessions > 4:
            break  # All deficits buffered — voluntary stop

        # Only consider jobs that produce a resource currently in deficit
        deficit_resources = {r for r, net in projected_net.items() if net < 0}
        curr_weights = _weights_from_net(
            projected_net, state["stockpiles"], prices, state["money_income"],
            state.get("resource_capacity"),
            active_resources=state.get("active_resources"), money_stock=state.get("money"),
        )
        job_scores = score_jobs(state, curr_weights, prices, stability_headroom=stability_headroom)
        positive = {}
        for k, v in job_scores.items():
            if v <= 0.05:
                continue
            job = state["available_jobs"].get(k, {})
            produces_deficit = any(
                r in deficit_resources
                for r, amt in job.get("production", {}).items()
                if isinstance(amt, (int, float)) and amt > 0
            )
            if produces_deficit:
                positive[k] = v
        if not positive:
            break  # No job can fix the deficit

        best = max(positive, key=lambda k: positive[k])
        assignments[best] = assignments.get(best, 0) + 1
        pops_remaining -= 1

        job = state["available_jobs"][best]
        for r, amt in job.get("production", {}).items():
            if isinstance(amt, (int, float)) and r in projected_net:
                projected_net[r] = projected_net.get(r, 0) + _consume_clamp_absorbed(clamp_absorbed, r, amt)
            elif isinstance(amt, (int, float)) and r in stability_headroom:
                _consume_headroom(stability_headroom, r, amt)
        for r, amt in job.get("upkeep", {}).items():
            if isinstance(amt, (int, float)) and r in projected_net:
                projected_net[r] = projected_net.get(r, 0) - amt

    upkeep_pops = total_idle - pops_remaining
    upkeep_ratio = upkeep_pops / total_idle if total_idle > 0 else 0.0

    # Identify resources still in critical deficit after upkeep exhausted options
    # (ran out of pops, or no job produces the resource). These are genuine
    # survival needs. When the loop stopped voluntarily (all deficits buffered
    # past 4 sessions) this scan finds nothing.
    unresolved = set()
    for r, net in projected_net.items():
        if net < 0:
            stockpile = state["stockpiles"].get(r, 0)
            sessions = stockpile / (-net) if stockpile > 0 else 0
            if sessions < 4:
                unresolved.add(r)

    for jk, cnt in sorted(assignments.items(), key=lambda x: -x[1]):
        if cnt > 0:
            job = state["available_jobs"].get(jk, {})
            log.append(f"Upkeep: {cnt}× {job.get('display_name', jk)}")
    if upkeep_pops > 0:
        log.append(f"Upkeep floor: {upkeep_pops}/{total_idle} pops ({upkeep_ratio:.0%} of workforce)")

    # Stash on state so district scoring can treat deficits jobs CANNOT fix as
    # genuinely critical while job-covered deficits stay an efficiency concern.
    state["unresolved_deficits"] = unresolved

    # {resource: pops pinned to producing it} — how much workforce each
    # resource's upkeep consumes. Used by stabilize_economy weighting.
    burden = {}
    for jk, cnt in assignments.items():
        if cnt <= 0:
            continue
        jdata = state["available_jobs"].get(jk, {})
        pr = _get_primary_resource(jdata)
        if pr:
            burden[pr] = burden.get(pr, 0) + cnt
    state["upkeep_burden"] = burden

    # Clamp-absorbed malus not yet overcome by upkeep jobs — goal job
    # assignment continues consuming from here.
    state["production_clamp_absorbed_remaining"] = clamp_absorbed

    # Stability chance headroom not yet used up by upkeep jobs — goal job
    # assignment continues consuming from here (mirrors clamp_absorbed above).
    state["stability_chance_headroom_remaining"] = stability_headroom

    return (
        {k: v for k, v in assignments.items() if v > 0},
        pops_remaining,
        projected_net,
        log,
        upkeep_ratio,
        unresolved,
    )


# ---------------------------------------------------------------------------
# Strategic goal selection
# ---------------------------------------------------------------------------

def _nation_in_active_war(nation):
    """Check if this nation is a participant in any war."""
    nation_id = str(nation.get("_id", ""))
    if not nation_id:
        return False
    try:
        link = mongo.db.war_links.find_one({"participant": nation_id})
        return link is not None
    except Exception:
        return False


def _count_available_techs(nation):
    """Count techs with met prerequisites that haven't been researched yet."""
    tech_data = json_data.get("tech", {})
    nation_techs = nation.get("technologies", {})
    researched = {k for k, v in nation_techs.items() if v.get("researched")}
    count = 0
    for tk, tdata in tech_data.items():
        if tk in researched:
            continue
        prereqs_all = tdata.get("prerequisites_all", [])
        prereqs_one = tdata.get("prerequisites_one", [])
        prereqs_two = tdata.get("prerequisites_two", [])
        if prereqs_all and not all(p in researched for p in prereqs_all):
            continue
        if prereqs_one and not any(p in researched for p in prereqs_one):
            continue
        if prereqs_two and sum(1 for p in prereqs_two if p in researched) < 2:
            continue
        count += 1
    return count


def select_strategic_goal(old_nation, state, personality, upkeep_ratio, prices=None):
    """
    Choose a single strategic goal based on personality, situation, upkeep burden, and persistence.
    Returns (selected_goal_dict, candidates_list).
    """
    candidates = []
    nation_stability = old_nation.get("stability", "Balanced")
    stab_gain = old_nation.get("stability_gain_chance", 0)
    stab_loss = old_nation.get("stability_loss_chance", 0)
    in_war = _nation_in_active_war(old_nation)
    infamy = old_nation.get("infamy", 0)
    available_tech_count = _count_available_techs(old_nation)
    pop_cap = old_nation.get("effective_pop_capacity", 0)
    pops = state["total_pops"]
    nation_techs = old_nation.get("technologies", {})
    has_archaic = nation_techs.get("archaic_inspirations", {}).get("researched", False)

    # Count deficits
    critical_count = sum(1 for r, s in state["sessions_until_empty"].items() if s < 2)
    deficit_count = sum(1 for r, net in state["net_production"].items() if net < 0)
    non_critical_deficit = deficit_count - critical_count

    # Money crisis: negative income with the treasury running dry soon
    if state["money_income"] < 0:
        money_sessions = state["money"] / -state["money_income"] if state["money"] > 0 else 0.0
    else:
        money_sessions = float("inf")
    money_crisis = money_sessions < 4

    # Count surplus resources
    surplus_count = sum(
        1 for r, s in state["sessions_until_empty"].items()
        if s == float("inf") and state["stockpiles"].get(r, 0) > 15
    )

    for goal_type, goal_def in STRATEGIC_GOALS.items():
        base = goal_def["base_urgency"]
        situation = 0.0
        rationale_parts = []

        # --- Personality bonus ---
        pers_bonus = sum(
            personality.get(dim, 0) * weight
            for dim, weight in goal_def["personality_dims"].items()
        )

        # --- Situation modifiers per goal ---
        if goal_type == "stabilize_economy":
            if deficit_count == 0 and not money_crisis:
                candidates.append({
                    "type": goal_type, "score": 0,
                    "display_name": goal_def["display_name"],
                    "rationale": "No deficits",
                })
                continue
            situation += critical_count * 20 + non_critical_deficit * 15
            if deficit_count:
                rationale_parts.append(f"{critical_count} critical, {non_critical_deficit} other deficits")
            if money_crisis:
                money_boost = 40 if money_sessions < 2 else 25
                situation += money_boost
                rationale_parts.append(f"money crisis ({money_sessions:.1f} sessions of gold left, +{money_boost})")
            if upkeep_ratio > 0.4:
                situation += 25
                rationale_parts.append(f"upkeep ratio {upkeep_ratio:.0%} (inefficient)")

        elif goal_type == "stabilize_nation":
            stab_situation = 0
            net_instability = max(0, stab_loss - stab_gain)
            instab_score = net_instability * 100
            if nation_stability == "Fragile":
                stab_situation += 60
                rationale_parts.append("Fragile stability (+60)")
            elif nation_stability == "Unsettled":
                stab_situation += 30
                rationale_parts.append("Unsettled stability (+30)")
            if instab_score > 5:
                stab_situation += instab_score
                rationale_parts.append(f"net instability {net_instability:.2f} (+{instab_score:.0f})")
            if stab_situation <= 0:
                candidates.append({
                    "type": goal_type, "score": 0,
                    "display_name": goal_def["display_name"],
                    "rationale": "Stability is healthy",
                })
                continue
            situation += stab_situation

        elif goal_type == "grow_economy":
            open_slots = state["open_district_slots"]
            slot_bonus = min(25, open_slots * 4)
            situation += slot_bonus
            rationale_parts.append(f"{open_slots} open district slots (+{slot_bonus})")
            if upkeep_ratio > 0.4:
                situation += 15
                rationale_parts.append(f"upkeep ratio {upkeep_ratio:.0%} (need better infrastructure)")
            if state["money_income"] > 0:
                situation += 10
                rationale_parts.append("money income positive")

        elif goal_type == "prepare_war":
            if in_war:
                situation += 60
                rationale_parts.append("ACTIVE WAR (+60)")
            if infamy < 5 and personality.get("aggression", 0) > 0.2:
                situation += 15
                rationale_parts.append("low infamy + aggressive")

        elif goal_type == "develop_technology":
            if available_tech_count == 0:
                candidates.append({
                    "type": goal_type, "score": 0,
                    "display_name": goal_def["display_name"],
                    "rationale": "No available techs",
                })
                continue
            if available_tech_count > 3:
                situation += 10
                rationale_parts.append(f"{available_tech_count} techs available")

        elif goal_type == "grow_population":
            if pop_cap > 0:
                cap_pct = pops / pop_cap * 100
                if cap_pct > 80:
                    pop_bonus = int((cap_pct - 80) * 5)
                    situation += pop_bonus
                    rationale_parts.append(f"pops {pops}/{pop_cap} ({cap_pct:.0f}% of cap, +{pop_bonus})")

                    can_grow = False
                    grow_avenue = None

                    # Check cities: open city slots, or a better city type to replace
                    city_slots = old_nation.get("city_slots", 0)
                    existing_cities = old_nation.get("cities", [])
                    cities_data = json_data.get("cities", {})
                    open_city_slots = max(0, city_slots - len(existing_cities))

                    _grow_researched = {k for k, v in old_nation.get("technologies", {}).items() if v.get("researched")}
                    if open_city_slots > 0:
                        can_grow = True
                        grow_avenue = f"{open_city_slots} open city slot(s)"
                    elif city_slots > 0 and existing_cities:
                        # Check if any available city type gives more pop cap than the weakest existing city
                        existing_caps = []
                        for c in existing_cities:
                            ct = c.get("type", "")
                            base_cap = cities_data.get(ct, {}).get("modifiers", {}).get("effective_pop_capacity", 0)
                            existing_caps.append((ct, base_cap))
                        if existing_caps:
                            worst_type, worst_cap = min(existing_caps, key=lambda x: x[1])
                            for ct, cdata in cities_data.items():
                                req_tech = cdata.get("requirements", {}).get("tech")
                                if req_tech and req_tech not in _grow_researched:
                                    continue
                                new_cap = cdata.get("modifiers", {}).get("effective_pop_capacity", 0)
                                if new_cap > worst_cap:
                                    can_grow = True
                                    grow_avenue = f"replace {worst_type} (+{worst_cap}) with {ct} (+{new_cap})"
                                    break

                    # Check districts if cities can't help
                    if not can_grow and state["open_district_slots"] > 0:
                        try:
                            from calculations.source_adapters import _resolve_modifier_type
                            for dd in mongo.db.district_defs.find({}, {"key": 1, "modifiers": 1, "_id": 0}):
                                if dd.get("key") in state["existing_def_keys"]:
                                    continue
                                for mod in dd.get("modifiers", []):
                                    if _resolve_modifier_type(mod) == "effective_pop_capacity":
                                        can_grow = True
                                        grow_avenue = f"district {dd.get('key', '?')}"
                                        break
                                if can_grow:
                                    break
                        except Exception:
                            can_grow = True

                    if not can_grow:
                        candidates.append({
                            "type": goal_type, "score": 0,
                            "display_name": goal_def["display_name"],
                            "rationale": "No city slots and no districts to increase pop capacity",
                        })
                        continue
                    if grow_avenue:
                        rationale_parts.append(f"avenue: {grow_avenue}")

        elif goal_type == "convert_population":
            pass  # no special gating — the goal drives building churches/homes

        elif goal_type == "expand_trade":
            if surplus_count > 0:
                situation += surplus_count * 5
                rationale_parts.append(f"{surplus_count} surplus resources (+{surplus_count * 5})")
            if money_crisis:
                # Selling resources for cash is the fastest fix for a treasury
                # bleeding out — this goal also boosts commerce districts.
                money_boost = 60 if money_sessions < 2 else 35
                situation += money_boost
                rationale_parts.append(f"money crisis ({money_sessions:.1f} sessions of gold left, +{money_boost})")

        elif goal_type == "build_wonder":
            if not has_archaic or deficit_count > 0 or surplus_count < 3:
                candidates.append({
                    "type": goal_type, "score": 0,
                    "display_name": goal_def["display_name"],
                    "rationale": "Prerequisites not met (need archaic_inspirations, no deficits, surplus)",
                })
                continue
            situation += 20
            rationale_parts.append("comfortable economy with surplus")

        raw_score = base + pers_bonus + situation

        # --- Temperament multiplier ---
        temp = old_nation.get("temperament", "Neutral")
        temp_mults = GOAL_TEMPERAMENT_MULT.get(temp, {})
        temp_mult = temp_mults.get(goal_type, 1.0)
        if temp_mult != 1.0:
            rationale_parts.append(f"{temp} temperament ×{temp_mult}")

        score = raw_score * temp_mult

        # --- Economy gate ---
        if critical_count >= 3 and goal_type not in ("stabilize_economy", "stabilize_nation"):
            score *= 0.3
            rationale_parts.append("economy gate ×0.3 (3+ critical deficits)")

        # --- Upkeep skew ---
        if upkeep_ratio > 0.4 and goal_type in ("grow_economy", "stabilize_economy"):
            score *= 1.5
            rationale_parts.append(f"upkeep skew ×1.5 (ratio {upkeep_ratio:.0%})")

        # --- Persistence bonus (decaying) ---
        previous_goal = old_nation.get("ai_state", {}).get("strategic_goal", {})
        persistence = 0
        if previous_goal.get("type") == goal_type:
            sessions_held = previous_goal.get("sessions_held", 1)
            persistence = max(0, 15 - (sessions_held - 1) * 3)
            if persistence > 0:
                rationale_parts.append(f"persistence +{persistence} (held {sessions_held} sessions)")

        score += persistence

        if pers_bonus != 0:
            rationale_parts.insert(0, f"personality +{pers_bonus:.0f}")

        candidates.append({
            "type": goal_type,
            "display_name": goal_def["display_name"],
            "score": round(score, 1),
            "rationale": "; ".join(rationale_parts) if rationale_parts else "base priority",
        })

    candidates.sort(key=lambda g: g["score"], reverse=True)
    selected = candidates[0] if candidates else {
        "type": "stabilize_economy", "display_name": "Stabilize Economy",
        "score": 0, "rationale": "fallback",
    }

    # Compute sessions_held for persistence tracking
    previous_goal = old_nation.get("ai_state", {}).get("strategic_goal", {})
    if previous_goal.get("type") == selected["type"]:
        selected["sessions_held"] = previous_goal.get("sessions_held", 0) + 1
    else:
        selected["sessions_held"] = 1

    return selected, candidates




# ---------------------------------------------------------------------------
# Dynamic market prices
# ---------------------------------------------------------------------------

def compute_market_prices(member_nations, base_prices=None):
    """
    Compute per-resource market prices from open buy/sell orders of member nations.
    Uses a volume-weighted average: many buy orders at high prices push prices up;
    many sell orders at low prices push prices down.
    Falls back to base_prices for resources with no open orders.
    """
    if base_prices is None:
        base_prices = _base_prices()

    totals = {}  # resource → [sum(price × qty), sum(qty)]
    for nation in member_nations:
        for desire in nation.get("resource_desires", []):
            r     = desire.get("resource")
            price = desire.get("price", 0)
            qty   = desire.get("quantity", 0)
            if not r or price <= 0 or qty <= 0:
                continue
            if "Buy" in desire.get("trade_type", "") or "Sell" in desire.get("trade_type", ""):
                if r not in totals:
                    totals[r] = [0.0, 0.0]
                totals[r][0] += price * qty
                totals[r][1] += qty

    prices = {}
    for r, base_p in base_prices.items():
        if r in totals and totals[r][1] > 0:
            prices[r] = round(totals[r][0] / totals[r][1], 2)
        else:
            prices[r] = float(base_p)
    return prices


def get_stored_market_prices(old_nation):
    """
    Return dynamic resource prices stored on the nation's market document(s).
    Prices are written each session by market_price_tick.
    Falls back to static base prices for any resource without market data.
    """
    nation_id = str(old_nation.get("_id", ""))
    combined  = {}

    try:
        market_links_db = category_data["market_links"]["database"]
        my_links = list(market_links_db.find({"member": nation_id}, {"market": 1}))
        for link in my_links:
            market_id = link.get("market")
            if not market_id:
                continue
            try:
                market_doc = mongo.db.markets.find_one(
                    {"_id": ObjectId(market_id)}, {"resource_prices": 1}
                )
                if market_doc and "resource_prices" in market_doc:
                    for r, p in market_doc["resource_prices"].items():
                        if p > combined.get(r, 0):
                            combined[r] = p  # take highest across multiple markets
            except Exception:
                pass
    except Exception:
        pass

    # Fill missing resources with static base prices
    base = _base_prices()
    for r, p in base.items():
        if r not in combined:
            combined[r] = float(p)
    return combined


def market_price_tick(old_nations, new_nations, schema):
    """
    Compute per-market resource prices from current session's member orders and store
    them on each market document. Runs as a NATION_CROSS_TICK_FUNCTION after AI Decision
    Tick so prices reflect the latest resource desires. The following session's AI reads
    these stored prices via get_stored_market_prices.
    """
    base   = _base_prices()
    log_lines = []

    try:
        market_links_db = category_data["market_links"]["database"]
        markets    = list(mongo.db.markets.find({}, {"_id": 1, "name": 1}))
        nation_idx = {str(n.get("_id", "")): n for n in new_nations}

        for market in markets:
            market_id    = str(market["_id"])
            member_links = list(market_links_db.find({"market": market_id}, {"member": 1}))
            member_ids   = {lnk["member"] for lnk in member_links}
            members      = [nation_idx[mid] for mid in member_ids if mid in nation_idx]
            if not members:
                continue

            prices = compute_market_prices(members, base)
            mongo.db.markets.update_one(
                {"_id": market["_id"]},
                {"$set": {"resource_prices": prices}}
            )
            mkt_name = market.get("name", market_id)
            log_lines.append(
                f"[{mkt_name}] Prices updated from {len(members)} members"
            )
    except Exception as e:
        log_lines.append(f"Market price tick error: {e}")

    return "\n".join(log_lines) + "\n" if log_lines else ""


# ---------------------------------------------------------------------------
# District scoring and planning
# ---------------------------------------------------------------------------

def _apply_district_cost_mod(raw_cost, cost_mod):
    """Apply a fractional cost modifier (e.g. -0.3 for Industrious) to a cost dict."""
    factor = max(0.1, 1.0 + cost_mod)
    return {r: max(1, math.ceil(amt * factor)) for r, amt in raw_cost.items()}


def _price_scale(resource_key, prices):
    """Return a normalisation factor so 1 unit of any resource maps to a comparable
    gold-equivalent value.  Money itself is scaled at 1/100 so that +150 money_income
    is comparable to +2 food (food base price ~50–100)."""
    if resource_key == "money":
        return 0.01
    return prices.get(resource_key, 50) / 50.0


def _district_modifier_value(modifiers_list, need_weights, prices, territory_types=None, nation_jobs=None, state=None, discretionary_info=None):
    """Sum the weighted value of a list of modifier objects against current needs,
    normalised by market price so money and resources are comparable.

    discretionary_info: optional dict with 'remaining_pops' (int) and 'job_shares'
    ({job_key: fraction}) pre-computed from job scores. Used to estimate how many
    discretionary pops would likely work each job.

    Returns (total, breakdown) where breakdown is a list of
    (field, raw_value, weight, price_scale, contribution, matched) tuples.
    """
    from calculations.source_adapters import _resolve_modifier_type
    terrains_data = json_data.get("terrains", {})
    territory_types = territory_types or {}
    nation_jobs = nation_jobs or {}
    _state_ref = state
    total = 0.0
    breakdown = []
    for m in modifiers_list or []:
        field = _resolve_modifier_type(m)
        mod_type = m.get("modifier_type", "")
        value = m.get("value", 0)
        contrib = 0.0
        weight = 0.0
        scale = 1.0
        matched = False

        # Terrain-rule modifiers: value depends on how many tiles the nation has
        if mod_type in ("terrain_production_multiplier", "terrain_count_required_delta",
                        "terrain_extra_resource", "terrain_swap_resource"):
            terrain_key = m.get("terrain", "")
            tile_count = territory_types.get(terrain_key, 0)
            if tile_count > 0:
                terrain_def = terrains_data.get(terrain_key, {})
                resource_key = m.get("resource") or terrain_def.get("resource", "")
                if resource_key and resource_key != "none" and resource_key in need_weights:
                    weight = need_weights[resource_key]
                    scale = _price_scale(resource_key, prices)
                    tile_factor = min(tile_count / 10.0, 3.0)
                    if mod_type == "terrain_production_multiplier":
                        contrib = weight * (value - 1.0) * tile_factor * scale
                    elif mod_type == "terrain_count_required_delta":
                        count_req = terrain_def.get("count_required", 4)
                        if count_req > 0:
                            extra_units = tile_count / max(1, count_req) - tile_count / max(1, count_req + value)
                            contrib = weight * extra_units * scale
                    elif mod_type in ("terrain_extra_resource", "terrain_swap_resource"):
                        if value > 0:
                            extra_units = tile_count / value
                            contrib = weight * extra_units * scale
                    matched = True
            total += contrib
            breakdown.append((field, value, round(weight, 2), round(scale, 2), round(contrib, 2), matched))
            continue

        # Job production modifiers: {job}_{resource}_production
        # Pops estimate = upkeep pops on this job + estimated discretionary pops.
        # Discretionary share is pre-computed from job scores in score_buildable_districts.
        if mod_type == "job_resource_production":
            job_key = m.get("job", "")
            resource_key = m.get("resource", "")

            # Upkeep pops already assigned to this job
            upkeep_pops = nation_jobs.get(job_key, 0)

            # Estimate discretionary pops likely to work this job
            disc_pops = 0.0
            if discretionary_info:
                remaining = discretionary_info.get("remaining_pops", 0)
                share = discretionary_info.get("job_shares", {}).get(job_key, 0)
                disc_pops = share * remaining

            pops_on_job = upkeep_pops + disc_pops

            # Fallback: if no upkeep pops and no discretionary estimate,
            # use deficit-based heuristic
            if pops_on_job < 0.5 and resource_key and resource_key in need_weights:
                net = _state_ref.get("net_production", {}).get(resource_key, 0) if _state_ref else 0
                if net < 0:
                    job_def = json_data.get("jobs", {}).get(job_key, {})
                    base_prod = job_def.get("production", {}).get(resource_key, 0)
                    boosted_prod = base_prod + value
                    if boosted_prod > 0:
                        pops_on_job = min(
                            math.ceil(-net / boosted_prod),
                            max(1, (_state_ref or {}).get("total_pops", 1) // 4),
                        )
                    else:
                        pops_on_job = 1
                else:
                    pops_on_job = max(1, pops_on_job)

            if resource_key and resource_key in need_weights and pops_on_job > 0:
                weight = need_weights[resource_key]
                scale = _price_scale(resource_key, prices)
                contrib = weight * value * pops_on_job * scale
                matched = True
            total += contrib
            breakdown.append((field, value, round(weight, 2), round(scale, 2), round(contrib, 2), matched))
            continue

        if field in need_weights:
            weight = need_weights[field]
            scale = _price_scale(field, prices)
            contrib = weight * value * scale
            matched = True
        elif field in PRODUCTION_FIELD_MAP:
            # Use the live need weight for the mapped stockpile when available
            # (mirrors score_jobs) — so e.g. money_income modifiers scale with
            # money urgency instead of a flat base weight.
            stockpile_key, base_w = PRODUCTION_FIELD_MAP[field]
            if stockpile_key and stockpile_key in need_weights:
                weight = need_weights[stockpile_key]
            else:
                weight = base_w
            scale = _price_scale(stockpile_key, prices) if stockpile_key else 1.0
            contrib = weight * value * scale
            matched = True
        elif field.endswith("_production"):
            resource_key = field[: -len("_production")]
            if resource_key in need_weights:
                weight = need_weights[resource_key]
                scale = _price_scale(resource_key, prices)
                contrib = weight * value * scale
                matched = True
        elif field.endswith("_node_value"):
            resource_key = field[: -len("_node_value")]
            if resource_key in need_weights:
                weight = need_weights[resource_key]
                scale = _price_scale(resource_key, prices) * 0.5
                contrib = weight * value * scale
                matched = True
        total += contrib
        breakdown.append((field, value, round(weight, 2), round(scale, 2), round(contrib, 2), matched))
    return total, breakdown


def _job_per_pop_score(jdata, need_weights, prices):
    """Score a single job per-pop: (prod_value, upkeep_cost, prod_lines, upkeep_lines, primary_resource)."""
    prod_value = 0.0
    prod_lines = []
    best_contrib = 0.0
    primary_resource = None
    for field, amount in jdata.get("production", {}).items():
        contrib = 0.0
        weight = 0.0
        scale = 1.0
        res_key = field
        if field in need_weights:
            weight = need_weights[field]
            scale = _price_scale(field, prices)
            contrib = weight * amount * scale
        elif field in PRODUCTION_FIELD_MAP:
            stockpile_key, base_w = PRODUCTION_FIELD_MAP[field]
            res_key = stockpile_key or field
            scale = _price_scale(stockpile_key, prices) if stockpile_key else 1.0
            if stockpile_key and stockpile_key in need_weights:
                weight = need_weights[stockpile_key]
                contrib = weight * amount * scale
            else:
                weight = base_w
                contrib = base_w * amount * scale
        elif field.endswith("_production"):
            res_key = field[: -len("_production")]
            if res_key in need_weights:
                weight = need_weights[res_key]
                scale = _price_scale(res_key, prices)
                contrib = weight * amount * scale
        prod_value += contrib
        prod_lines.append({
            "field": field, "amount": amount,
            "weight": round(weight, 2), "scale": round(scale, 2),
            "contrib": round(contrib, 2),
        })
        if contrib > best_contrib:
            best_contrib = contrib
            primary_resource = res_key

    upkeep_cost = 0.0
    upkeep_lines = []
    for field, amount in jdata.get("upkeep", {}).items():
        w = need_weights.get(field, 1.0)
        scale = _price_scale(field, prices)
        contrib = w * amount * scale
        upkeep_cost += contrib
        upkeep_lines.append({
            "field": field, "amount": amount,
            "weight": round(w, 2), "scale": round(scale, 2),
            "contrib": round(contrib, 2),
        })

    return prod_value, upkeep_cost, prod_lines, upkeep_lines, primary_resource


def _get_primary_resource(jdata):
    """Return the resource key for the job's highest-amount production field."""
    best_amt = 0
    primary = None
    for field, amount in jdata.get("production", {}).items():
        if not isinstance(amount, (int, float)) or amount <= 0:
            continue
        res = field
        if field in PRODUCTION_FIELD_MAP:
            res = PRODUCTION_FIELD_MAP[field][0] or field
        elif field.endswith("_production"):
            res = field[: -len("_production")]
        if amount > best_amt:
            best_amt = amount
            primary = res
    return primary


def _unlocked_jobs_value(district_key, state, need_weights, prices, nation_jobs):
    """Score the jobs that would become available if this district were built.

    Compares each new job against existing jobs that fill the same role (produce
    the same primary resource).  When an existing producer exists, the value is
    the marginal improvement times the pops currently on that producer.  When no
    existing producer exists, estimates how many pops could work the new job
    while maintaining positive income compared to the weakest assigned job.

    Returns (total_value, [job_detail_dict, ...], near_miss_dict_or_None).
    near_miss is the closest-to-profitable comparison that was rejected
    (marginal <= 0), so callers can explain why nothing unlocked.
    """
    jobs_data = json_data.get("jobs", {})
    available_jobs = state.get("available_jobs", {})
    current_districts = state["existing_def_keys"] | state["existing_types"]
    hypothetical_districts = current_districts | {district_key}

    # Pre-compute per-pop scores for all currently available jobs
    existing_scores = {}
    existing_by_resource = {}
    for ejk, ejdata in available_jobs.items():
        pops_on = nation_jobs.get(ejk, 0)
        prod_v, upk_v, _, _, _ = _job_per_pop_score(ejdata, need_weights, prices)
        ej_score = max(0.0, prod_v - upk_v)
        existing_scores[ejk] = ej_score
        if pops_on > 0:
            primary = _get_primary_resource(ejdata)
            if primary:
                existing_by_resource.setdefault(primary, []).append(
                    (ejk, ej_score, pops_on)
                )

    # Weakest currently assigned job (fallback for new-role comparison)
    assigned_scores = [(jk, existing_scores.get(jk, 0)) for jk, cnt in nation_jobs.items() if cnt > 0 and jk in existing_scores]
    weakest_score = min((s for _, s in assigned_scores), default=0.0)

    total = 0.0
    unlocked = []
    near_miss = None  # best rejected comparison, for transparency when nothing unlocks
    for jk, jdata in jobs_data.items():
        if jk in available_jobs:
            continue
        req_districts = jdata.get("requirements", {}).get("district", [])
        if not req_districts:
            continue
        if any(d in current_districts for d in req_districts):
            continue
        if not any(d in hypothetical_districts for d in req_districts):
            continue
        non_district_reqs = {k: v for k, v in jdata.get("requirements", {}).items() if k != "district"}
        if non_district_reqs:
            continue

        prod_value, upkeep_cost, prod_lines, upkeep_lines, primary_resource = \
            _job_per_pop_score(jdata, need_weights, prices)
        raw_score = max(0.0, prod_value - upkeep_cost)
        if raw_score <= 0:
            continue

        # Find the best comparison: check ALL resources this job produces
        # against existing producers, not just the weighted-primary.
        # A farmer producing food AND stability_gain_chance should be compared
        # against food producers (hunters) if that gives a better scaled score.
        best_comparison = None
        all_produced_resources = set()
        for field, amt in jdata.get("production", {}).items():
            if not isinstance(amt, (int, float)) or amt <= 0:
                continue
            res = field
            if field in PRODUCTION_FIELD_MAP:
                res = PRODUCTION_FIELD_MAP[field][0] or field
            elif field.endswith("_production"):
                res = field[:-len("_production")]
            all_produced_resources.add(res)

        # Track the best LOSING comparison too — if any resource already has an
        # incumbent producer, this job is competing for an existing role, even
        # if it loses. It must never silently fall through to "new role" just
        # because it doesn't beat that incumbent (that was comparing fisherman
        # against the weakest job in the nation instead of against farmer,
        # the resource's actual direct competitor).
        has_incumbent = False
        best_losing_comparison = None
        for res in all_produced_resources:
            same_role = existing_by_resource.get(res, [])
            if same_role:
                has_incumbent = True
                best_ex_name, best_ex_score, _ = max(same_role, key=lambda x: x[1])
                m = raw_score - best_ex_score
                ep = sum(p for _, _, p in same_role)
                if m > 0:
                    scaled = m * ep
                    if best_comparison is None or scaled > best_comparison[0]:
                        best_comparison = (
                            scaled, m, ep, best_ex_score,
                            available_jobs[best_ex_name].get("display_name", best_ex_name),
                            "replaces",
                        )
                elif best_losing_comparison is None or m > best_losing_comparison[1]:
                    best_losing_comparison = (
                        0, m, ep, best_ex_score,
                        available_jobs[best_ex_name].get("display_name", best_ex_name),
                        "replaces",
                    )

        if best_comparison:
            _, marginal, est_pops, comparison_score, comparison_name, comparison_type = best_comparison
        elif has_incumbent:
            # Loses to its direct competitor for every resource it produces —
            # record as a near-miss against the actual incumbent, not a fake new role.
            _, marginal, est_pops, comparison_score, comparison_name, comparison_type = best_losing_comparison
        else:
            # New role — no existing producer for any of this job's resources
            marginal = raw_score - weakest_score
            net_prod = state["net_production"].get(primary_resource, 0)
            job_net = 0
            for field, amt in jdata.get("production", {}).items():
                res = field
                if field.endswith("_production"):
                    res = field[: -len("_production")]
                elif field in PRODUCTION_FIELD_MAP:
                    res = PRODUCTION_FIELD_MAP[field][0] or field
                if res == primary_resource and isinstance(amt, (int, float)):
                    job_net += amt
            for field, amt in jdata.get("upkeep", {}).items():
                if field == primary_resource and isinstance(amt, (int, float)):
                    job_net -= amt

            if net_prod < 0 and job_net > 0:
                est_pops = min(math.ceil(-net_prod / job_net), max(1, state["total_pops"] // 4))
            else:
                est_pops = 1
            comparison_score = weakest_score
            comparison_name = "weakest assigned job"
            comparison_type = "new role"

        if marginal <= 0:
            if near_miss is None or marginal > near_miss["marginal"]:
                near_miss = {
                    "name": jdata.get("display_name", jk),
                    "raw_score": round(raw_score, 2),
                    "comparison_score": round(comparison_score, 2),
                    "comparison_name": comparison_name,
                    "marginal": round(marginal, 2),
                }
            continue

        scaled = marginal * est_pops
        total += scaled
        unlocked.append({
            "name": jdata.get("display_name", jk),
            "raw_score": round(raw_score, 2),
            "comparison_score": round(comparison_score, 2),
            "comparison_name": comparison_name,
            "comparison_type": comparison_type,
            "marginal": round(marginal, 2),
            "est_pops": est_pops,
            "scaled_score": round(scaled, 2),
            "production": prod_lines,
            "upkeep": upkeep_lines,
        })

    return total, unlocked, near_miss


def score_buildable_districts(old_nation, state, need_weights, market_buy_prices, job_assignments=None):
    """
    Score all districts the nation could build.
    Returns list of (score, def_key_or_type, display_name, actual_cost, rationale).
    DB-driven districts take priority; legacy JSON districts also considered.
    job_assignments: {job_key: pop_count} from the current or simulated assignment.
    """
    from calculations.field_calculations import check_district_requirements

    if state["open_district_slots"] <= 0:
        return []

    nation_jobs = job_assignments if job_assignments is not None else old_nation.get("jobs", {})
    results = []

    # Deficits that upkeep jobs could NOT cover are genuine survival problems —
    # score districts against their full critical urgency. Job-covered deficits
    # deliberately keep their post-upkeep weight (an efficiency concern handled
    # by the upkeep-burden credit below), so trade/job urgency is untouched.
    unresolved = state.get("unresolved_deficits") or set()
    if unresolved:
        need_weights = dict(need_weights)
        for r in unresolved:
            need_weights[r] = max(need_weights.get(r, 0), 5.0)

    # Pre-compute discretionary pop info once for all district evaluations.
    upkeep_total = sum(nation_jobs.values()) if nation_jobs else 0
    remaining_pops = max(0, state["idle_pops"] - upkeep_total)
    job_shares = {}
    all_job_scores = score_jobs(state, need_weights, market_buy_prices)
    if remaining_pops > 0:
        total_positive = sum(max(0, s) for s in all_job_scores.values())
        if total_positive > 0:
            job_shares = {
                jk: max(0, sc) / total_positive
                for jk, sc in all_job_scores.items()
                if sc > 0
            }
    discretionary_info = {"remaining_pops": remaining_pops, "job_shares": job_shares}

    # Upkeep-burden credit inputs: which resources have pops pinned to sustaining
    # them, how much one pop produces of each, and what a freed pop would be worth
    # (median positive job score — the job it would take instead).
    jobs_json = json_data.get("jobs", {})
    upkeep_burden = {}   # {resource: pops pinned to jobs whose primary output is it}
    per_pop_prod = {}    # {resource: per-pop production of the covering job}
    for jk, cnt in (nation_jobs or {}).items():
        if not isinstance(cnt, (int, float)) or cnt <= 0:
            continue
        jdata = state["available_jobs"].get(jk) or jobs_json.get(jk, {})
        pr = _get_primary_resource(jdata)
        if not pr:
            continue
        amt = jdata.get("production", {}).get(pr, 0)
        if not isinstance(amt, (int, float)) or amt <= 0:
            continue
        upkeep_burden[pr] = upkeep_burden.get(pr, 0) + cnt
        per_pop_prod[pr] = max(per_pop_prod.get(pr, 0), amt)
    positive_job_scores = sorted(s for s in all_job_scores.values() if s > 0)
    pop_opportunity_value = (
        positive_job_scores[len(positive_job_scores) // 2]
        if positive_job_scores else 0.0
    )

    # Build a merged nation view for requirement checks that includes
    # districts built earlier this session (state["existing_def_keys"] is updated
    # in the build loop but old_nation["districts"] is not)
    req_check_nation = old_nation
    if state.get("existing_def_keys"):
        existing_keys_on_nation = {
            d.get("def_key", "") for d in old_nation.get("districts", []) if isinstance(d, dict)
        }
        newly_built = state["existing_def_keys"] - existing_keys_on_nation
        if newly_built:
            req_check_nation = dict(old_nation)
            req_check_nation["districts"] = list(old_nation.get("districts", [])) + [
                {"def_key": k} for k in newly_built
            ]

    # Pre-compute legal placement tiles with node info for node/synergy scoring
    from calculations.field_calculations import _compute_legal_placement
    legal_placement = _compute_legal_placement(req_check_nation)

    # Wealth factor: measures how liquid the nation is vs. a "comfortable" benchmark.
    # Used to scale the save_penalty for non-affordable districts — when the nation
    # is sitting on large stockpiles it should prefer spending NOW (strong penalty
    # for saving); when stockpiles are thin it's OK to wait (weak/no penalty).
    total_liquid = sum(
        amount * _price_scale(r, market_buy_prices)
        for r, amount in state.get("stockpiles", {}).items()
        if amount > 0
    )
    wealth_factor = min(1.0, total_liquid / 150.0)
    # save_penalty: 1.0 (no penalty) when wealth_factor=0, 0.5 when fully saturated.
    save_penalty = round(1.0 - 0.5 * wealth_factor, 3)

    # --- DB-driven district defs (use per-tick cache when available) ---
    global _DISTRICT_DEFS_CACHE
    if _DISTRICT_DEFS_CACHE is not None:
        defs = _DISTRICT_DEFS_CACHE
    else:
        try:
            defs = list(mongo.db.district_defs.find({}, {"_id": 0}))
        except Exception:
            defs = []

    for dd in defs:
        dk = dd.get("key", "")
        if not dk:
            continue
        # Skip already-built districts unless map_count allows more instances
        existing_count = state.get("existing_def_key_counts", {}).get(dk, 0)
        max_count = dd.get("map_count", 1)
        if existing_count >= max_count:
            continue

        if not check_district_requirements(req_check_nation, dd):
            continue

        raw_cost    = dd.get("cost", {})
        actual_cost = _apply_district_cost_mod(raw_cost, state["district_cost_mod"])

        can_afford_now = all(
            state["stockpiles"].get(r, 0) >= amt
            for r, amt in actual_cost.items()
            if r != "money"
        ) and state["money"] >= actual_cost.get("money", 0)

        mod_value, mod_breakdown = _district_modifier_value(dd.get("modifiers", []), need_weights, market_buy_prices, state.get("territory_types"), nation_jobs, state, discretionary_info)

        job_value, unlocked_jobs, job_near_miss = _unlocked_jobs_value(dk, state, need_weights, market_buy_prices, nation_jobs)

        # Node value: score the best available node on legal tiles for this district
        # For free_placement districts, use city-style tile scoring (any owned tile)
        node_value = 0.0
        node_desc = ""
        is_free_placement = dd.get("free_placement", False)
        tile_req = dd.get("tile_requirement", "land")

        map_count = dd.get("map_count", 1)
        if is_free_placement and tile_req in ("land", "coastal"):
            _bp = market_buy_prices if market_buy_prices else _base_prices()
            if map_count <= 1:
                tile_coord, tile_score, tile_rationale = _score_best_city_tile(
                    legal_placement, dk, need_weights, _bp
                )
                if tile_score > 0:
                    node_value = tile_score
                    node_desc = f"placement ({tile_coord[0]},{tile_coord[1]}): {tile_rationale}" if tile_coord else ""
            else:
                # Score all tiles and take the best N
                all_tile_scores = _score_all_city_tiles(
                    legal_placement, dk, need_weights, _bp
                )
                top_tiles = all_tile_scores[:map_count]
                node_value = sum(ts[1] for ts in top_tiles) if top_tiles else 0.0
                if top_tiles and node_value > 0:
                    tile_descs = [f"({ts[0][0]},{ts[0][1]})" for ts in top_tiles if ts[0]]
                    node_desc = f"{map_count} placements: {', '.join(tile_descs)} +{node_value:.1f}"

        if tile_req == "water":
            available_nodes = legal_placement.get("water_nodes", [])
        elif tile_req == "coastal":
            available_nodes = legal_placement.get("coastal_nodes", [])
        else:
            available_nodes = legal_placement.get("land_nodes", [])

        synergies = dd.get("synergies", [])
        luxury_set = set(_luxury_keys())
        best_node_value = 0.0
        best_node_res = ""
        for node_res in available_nodes:
            nv = 0.0
            is_lux = node_res in luxury_set
            prod_amount = 1 if is_lux else 2
            w = need_weights.get(node_res, 1.3)
            nv += w * prod_amount * _price_scale(node_res, market_buy_prices)
            # Synergy bonus if this node matches
            for syn in synergies:
                req = syn.get("requirement", "")
                matches = False
                if isinstance(req, list):
                    matches = node_res in [r.strip() for r in req]
                elif isinstance(req, str):
                    matches = node_res == req.strip()
                if matches and syn.get("modifiers"):
                    syn_val, _ = _district_modifier_value(
                        syn["modifiers"], need_weights, market_buy_prices,
                        state.get("territory_types"), nation_jobs, state,
                    )
                    nv += syn_val
            if nv > best_node_value:
                best_node_value = nv
                best_node_res = node_res
        node_value = best_node_value
        if best_node_res:
            node_desc = f"{best_node_res} node +{node_value:.1f}"

        # Metropolis adjacency bonus: if the nation has a metropolis, a new district
        # placed adjacent to it increases its pop cap. Each adjacent building/water
        # tile contributes to the metropolis's tiered bonus (1/3/5 adj → +1/+2/+3 pop cap).
        metro_coords = legal_placement.get("metropolis_coords", [])
        if metro_coords and tile_req in ("land", "coastal"):
            from calculations.field_calculations import _hex_neighbors as _axial_nb
            metro_adj_bonus = 0
            for tile_info in legal_placement.get("legal_land_tiles", []):
                coord = tile_info["coord"]
                for mc in metro_coords:
                    if coord in _axial_nb(*mc):
                        metro_adj_bonus = 3.0
                        break
                if metro_adj_bonus > 0:
                    break
            if metro_adj_bonus > 0:
                node_value += metro_adj_bonus
                if node_desc:
                    node_desc += f"; metropolis adj +{metro_adj_bonus:.1f}"
                else:
                    node_desc = f"metropolis adj +{metro_adj_bonus:.1f}"

        # Admin range bonus: districts granting +administration are more valuable
        # when the nation has OOR tiles that more admin would bring into range
        oor_count = len(legal_placement.get("oor_tiles", set()))
        if oor_count > 0:
            admin_grant = 0
            for m in dd.get("modifiers", []):
                if m.get("modifier_type") == "administration":
                    admin_grant += m.get("value", 0)
            if admin_grant > 0:
                admin_bonus = min(10, oor_count * 0.3) * admin_grant
                node_value += admin_bonus
                if node_desc:
                    node_desc += f"; +admin ({oor_count} OOR tiles, +{admin_bonus:.1f})"
                else:
                    node_desc = f"+admin ({oor_count} OOR tiles, +{admin_bonus:.1f})"

        market_bonus = sum(
            need_weights.get(r, 0.5) * v
            for r, v in market_buy_prices.items()
            if any(
                m.get("value", 0) > 0 and
                _try_resolve_field(m) == r
                for m in dd.get("modifiers", [])
            )
        ) * 0.3

        cost_penalty = sum(
            need_weights.get(r, 1.0) * amt * _price_scale(r, market_buy_prices)
            for r, amt in actual_cost.items()
            if r != "money"
        ) * 0.05 + (actual_cost.get("money", 0) / max(state["money"] + 1, 1)) * 5

        # Recurring upkeep penalty — unlike the one-time build cost, this is paid
        # every session forever, so it's weighted much more heavily per unit.
        recurring_upkeep = dd.get("upkeep", {})
        upkeep_penalty = sum(
            need_weights.get(r, 1.0) * amt * _price_scale(r, market_buy_prices)
            for r, amt in recurring_upkeep.items()
            if r != "money"
        ) * 2.0 + (recurring_upkeep.get("money", 0) / max(state["money"] + 1, 1)) * 10

        # Upkeep-burden (efficiency) credit: district production that replaces
        # what pinned upkeep pops currently produce frees those pops for better
        # jobs. Valued at the freed pop count × median positive job score.
        # This is where a Farm beats a Ranch for a nation feeding itself with
        # hunters — without ever marking food "critical" for trade purposes.
        efficiency_credit = 0.0
        pops_freed = 0.0
        if upkeep_burden and pop_opportunity_value > 0:
            for bd_field, bd_val, *_rest in mod_breakdown:
                if not bd_field.endswith("_production") or bd_val <= 0:
                    continue
                rk = bd_field[: -len("_production")]
                if rk in upkeep_burden and per_pop_prod.get(rk, 0) > 0:
                    pops_freed += min(upkeep_burden[rk], bd_val / per_pop_prod[rk])
            if pops_freed > 0:
                efficiency_credit = pops_freed * pop_opportunity_value

        score = mod_value + job_value + node_value + market_bonus + efficiency_credit - cost_penalty - upkeep_penalty

        # Non-affordable districts get a save_penalty scaled by how liquid the
        # nation is: large stockpiles → strong preference to spend now → steep
        # penalty on districts that can't be built immediately; small stockpiles
        # → it's fine to save a few sessions for the better option.
        if not can_afford_now and score > 0:
            score *= save_penalty

        parts = [f"modifiers {mod_value:.1f}"]
        if efficiency_credit > 0:
            parts.append(f"frees ~{pops_freed:.1f} upkeep pops (+{efficiency_credit:.1f})")
        if unlocked_jobs:
            job_names = ", ".join(f"{j['name']} ({j['scaled_score']})" for j in unlocked_jobs)
            parts.append(f"unlocks jobs +{job_value:.1f} [{job_names}]")
        elif job_near_miss:
            parts.append(
                f"job swap considered: {job_near_miss['name']} ({job_near_miss['raw_score']}) "
                f"vs {job_near_miss['comparison_name']} ({job_near_miss['comparison_score']}) — not worth it"
            )
        if node_desc:
            parts.append(node_desc)
        if market_bonus > 0.5:
            parts.append(f"market +{market_bonus:.1f}")
        parts.append(f"cost penalty -{cost_penalty:.1f}")
        if upkeep_penalty > 0.05:
            parts.append(f"upkeep penalty -{upkeep_penalty:.1f}")
        if can_afford_now:
            parts.append("[can afford]")
        else:
            parts.append(f"[saving ×{save_penalty:.2f}]")
        rationale = "; ".join(parts)

        results.append((score, dk, dd.get("display_name", dk), actual_cost, rationale, "db", mod_breakdown, unlocked_jobs))

    results.sort(key=lambda x: x[0], reverse=True)
    return results


def _try_resolve_field(m):
    """Safe wrapper for _resolve_modifier_type."""
    try:
        from calculations.source_adapters import _resolve_modifier_type
        return _resolve_modifier_type(m)
    except Exception:
        return ""


def _insert_filling_empty_slot(items, new_entry, is_empty_fn):
    """Insert new_entry into the first empty slot in items (in place), or
    append if none is empty. Mutates and returns items.

    Nation pages only render up to the nation's slot-count limit
    (district_slots / city_slots) at the matching array index, so a newly
    built district/city that gets appended past an existing empty slot can
    end up beyond that limit and never be shown — even though a real slot
    was sitting empty earlier in the list. Filling the first empty slot
    keeps new builds within the visible range whenever room already exists.
    """
    for i, entry in enumerate(items):
        if is_empty_fn(entry):
            items[i] = new_entry
            return items
    items.append(new_entry)
    return items


def update_district_plan(old_nation, new_nation, state, goals, personality, market_buy_prices, need_weights, log, job_assignments=None):
    """
    If the nation has an existing plan, check if it's still best and affordable.
    Otherwise pick the top-scored district as the new plan.
    Builds immediately if affordable.
    Returns updated plan dict (or None) and appends to log.
    """
    current_plan = old_nation.get("ai_state", {}).get("planned_district")
    scored       = score_buildable_districts(old_nation, state, need_weights, market_buy_prices, job_assignments)
    # Negative-scored entries are kept in results for debuggability only —
    # never plan or build them.
    scored       = [s for s in scored if s[0] > 0]

    if not scored:
        return None

    best_score, best_key, best_name, best_cost, best_rationale, best_src = scored[0][:6]

    # If current plan is still in top-3 candidates, keep it (avoid constant churn)
    top_keys = {s[1] for s in scored[:3]}
    if current_plan and current_plan.get("key") in top_keys:
        plan = current_plan
        plan["sessions_saving"] = plan.get("sessions_saving", 0) + 1
    else:
        plan = {
            "key":             best_key,
            "display_name":    best_name,
            "cost":            best_cost,
            "rationale":       best_rationale,
            "sessions_saving": 0,
            "source":          best_src,
        }
        log.append(f"New district goal: {best_name} — {best_rationale}")

    # Attempt to build if affordable
    key      = plan["key"]
    cost     = plan.get("cost", {})
    money_ok = state["money"] >= cost.get("money", 0)
    res_ok   = all(state["stockpiles"].get(r, 0) >= amt for r, amt in cost.items() if r != "money")

    if money_ok and res_ok and state["open_district_slots"] > 0:
        # Build it
        new_entry = {"def_key": key, "node": "", "upgrades": []} if plan["source"] == "db" \
               else {"type": key, "node": "", "era": 1}
        districts = list(new_nation.get("districts", deepcopy(old_nation.get("districts", []))))
        _insert_filling_empty_slot(
            districts, new_entry,
            lambda d: not d.get("def_key") and not d.get("type"),
        )
        new_nation["districts"] = districts

        # Deduct cost
        storage = dict(new_nation.get("resource_storage", deepcopy(old_nation.get("resource_storage", {}))))
        for r, amt in cost.items():
            if r == "money":
                new_nation["money"] = new_nation.get("money", old_nation.get("money", 0)) - amt
            else:
                storage[r] = storage.get(r, 0) - amt
        new_nation["resource_storage"] = storage

        log.append(f"Built district: {plan['display_name']} (saved {plan['sessions_saving']} sessions)")
        return None  # Plan fulfilled

    sessions = plan["sessions_saving"]
    log.append(f"Saving for: {plan['display_name']} (session {sessions + 1}) — {plan['rationale']}")
    return plan


# ---------------------------------------------------------------------------
# Resource desires (replaces random ai_resource_desire_tick)
# ---------------------------------------------------------------------------

def generate_resource_desires(state, goals, personality, prices=None):
    """
    Build a list of resource_desire dicts based on actual production/consumption.
    Limited to trade_slots. Buy orders for deficits; sell orders for surpluses.
    `prices` should be dynamic market prices; falls back to static base prices.
    """
    prices        = prices if prices is not None else _base_prices()
    common        = _general_and_unique_keys()
    luxury        = _luxury_keys()
    trade_slots   = max(0, state["trade_slots"])
    desires       = []

    sell_bias  = 1.0 + 0.3 * personality.get("trade", 0)

    buy_candidates  = []
    sell_candidates = []

    for r in common + luxury:
        net      = state["net_production"].get(r, 0)
        stockpile = state["stockpiles"].get(r, 0)
        sessions  = state["sessions_until_empty"].get(r, float("inf"))
        mkt       = prices.get(r, 10)
        is_luxury = r in luxury

        # --- Buy order: pay a premium above market to attract sellers ---
        if net < 0 or sessions < 5:
            urgency  = max(0.0, 1.0 - sessions / 5.0)
            mult     = 1.05 + urgency * 0.15          # 1.05 – 1.20× market price
            price    = int(round(mkt * mult / 5)) * 5
            qty      = 1 if is_luxury else min(5, max(1, int(abs(net) * 2 + 1)))
            ttype    = "Need to Buy" if urgency > 0.5 else "Desire to Buy"
            buy_candidates.append({
                "resource": r, "trade_type": ttype, "price": price,
                "quantity": qty, "_urgency": urgency,
            })

        # --- Sell order: discount market price slightly to attract buyers ---
        elif not is_luxury and sessions == float("inf") and stockpile > 20:
            surplus_sessions = stockpile / max(state["net_production"].get(r, 0.01), 0.01)
            if surplus_sessions > 6:
                price = int(round(mkt * (0.90 * sell_bias) / 5)) * 5
                price = max(price, int(mkt * 0.70 / 5) * 5)  # floor: 70% of market
                qty   = min(5, max(1, int(state["net_production"].get(r, 1) * 1.5)))
                ttype = "Need to Sell" if surplus_sessions > 12 else "Desire to Sell"
                sell_candidates.append({
                    "resource": r, "trade_type": ttype, "price": price,
                    "quantity": qty, "_urgency": surplus_sessions,
                })

    buy_candidates.sort(key=lambda x: x["_urgency"], reverse=True)
    sell_candidates.sort(key=lambda x: x["_urgency"], reverse=True)

    for entry in buy_candidates + sell_candidates:
        if len(desires) >= trade_slots:
            break
        clean = {k: v for k, v in entry.items() if not k.startswith("_")}
        desires.append(clean)

    return desires


# ---------------------------------------------------------------------------
# Goal-aware district evaluation
# ---------------------------------------------------------------------------

def _estimate_city_pop_cap(city_type, cities_data, nation):
    """
    Estimate total effective_pop_capacity a city type would provide for this nation,
    including conditional _modifiers evaluated against the nation's current state.
    """
    from calculations.scaling_methods import get_scaling_multiplier

    cdata = cities_data.get(city_type, {})
    total = cdata.get("modifiers", {}).get("effective_pop_capacity", 0)

    for m in cdata.get("_modifiers", []):
        if m.get("modifier_type") != "effective_pop_capacity":
            continue
        condition_scaling = m.get("condition_scaling", "")
        if not condition_scaling:
            total += m.get("value", 0)
            continue
        try:
            cond_x = float(m.get("condition_scaling_x") or 1)
            cond_extra = m.get("condition_scaling_extra") or ""
            cond_op = m.get("condition_operator") or ">="
            cond_val = float(m.get("condition_value") or 0)
            actual = get_scaling_multiplier(
                condition_scaling, nation,
                scaling_x=cond_x, scaling_extra=cond_extra
            )
            met = (
                (cond_op == ">=" and actual >= cond_val) or
                (cond_op == ">"  and actual >  cond_val) or
                (cond_op == "<=" and actual <= cond_val) or
                (cond_op == "<"  and actual <  cond_val) or
                (cond_op == "==" and actual == cond_val)
            )
            if met:
                total += m.get("value", 0)
        except Exception:
            pass

    return total


def _score_tile(tile_info, city_type, need_weights, prices, luxury_set):
    """Score a single tile for city/district placement by node value,
    admin range, and metropolis bonuses."""
    score = 0
    parts = []

    node_on = tile_info.get("node")
    if node_on:
        is_lux = node_on in luxury_set
        prod = 1 if is_lux else 2
        w = need_weights.get(node_on, 1.3)
        val = w * prod * 2 * _price_scale(node_on, prices)
        score += val
        parts.append(f"{node_on} on tile (+{val:.1f})")

    for adj_node in tile_info.get("adj_nodes", []):
        is_lux = adj_node in luxury_set
        prod = 1 if is_lux else 2
        w = need_weights.get(adj_node, 1.3)
        val = w * prod * _price_scale(adj_node, prices)
        score += val

    if tile_info.get("adj_nodes"):
        parts.append(f"{len(tile_info['adj_nodes'])} adj nodes")

    # Range extension bonus: value OOR tiles this building would bring into range
    nearby_oor = tile_info.get("nearby_oor", 0)
    if nearby_oor > 0:
        range_val = nearby_oor * 0.5
        score += range_val
        parts.append(f"extends range to {nearby_oor} tiles (+{range_val:.1f})")
        # Bonus for OOR nodes that become active
        for oor_node in tile_info.get("nearby_oor_nodes", []):
            is_lux = oor_node in luxury_set
            prod = 1 if is_lux else 2
            w = need_weights.get(oor_node, 1.3)
            val = w * prod * _price_scale(oor_node, prices) * 0.5
            score += val

    if city_type == "metropolis":
        adj_count = tile_info.get("adj_water_or_building", 0)
        if adj_count >= 5:
            metro_pop = 3
        elif adj_count >= 3:
            metro_pop = 2
        elif adj_count >= 1:
            metro_pop = 1
        else:
            metro_pop = 0
        score += metro_pop * 5
        if metro_pop > 0:
            parts.append(f"metropolis +{metro_pop} pop cap ({adj_count} adj)")

    return tile_info["coord"], score, ", ".join(parts) if parts else ""


def _score_all_city_tiles(legal_placement, city_type, need_weights, prices):
    """Score every legal city tile (anywhere in owned territory, not adjacency-
    restricted like districts) by node-claiming potential. Returns sorted
    list of (coord, score, rationale). Tiles with no nearby nodes still get
    included at score 0 so a city always has somewhere to go when one is
    needed, even if no tile offers a node bonus."""
    luxury_set = set(_luxury_keys())
    results = []
    for tile_info in legal_placement.get("legal_city_tiles", []):
        coord, score, rationale = _score_tile(tile_info, city_type, need_weights, prices, luxury_set)
        if coord:
            results.append((coord, score, rationale))
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def _score_best_city_tile(legal_placement, city_type, need_weights, prices):
    """Find the best legal land tile to place a city/district on.
    Returns (best_coord, best_score, rationale) or (None, 0, "").
    """
    results = _score_all_city_tiles(legal_placement, city_type, need_weights, prices)
    if results:
        return results[0]
    return None, 0, ""


def _nation_is_nomadic(nation):
    """Nomadic nations roam — their districts and cities exist on the nation
    doc only and are never placed on hex map tiles."""
    if nation.get("is_nomadic"):
        return True
    gov = nation.get("government_type", "")
    try:
        laws = category_data["nations"]["schema"]["properties"]["government_type"]["laws"]
        return laws.get(gov, {}).get("nomadic", 0) > 0
    except Exception:
        return False


def _pick_district_tile(legal_placement, dd, key, need_weights, prices):
    """Pick the best legal tile to place a district on, reusing the same
    node-value scoring as city placement (_score_tile).

    Regular districts are restricted to tiles adjacent to an existing city/
    district/wonder (legal_land_tiles / legal_water_tiles, depending on
    tile_requirement). free_placement districts may go anywhere in owned
    territory matching their terrain limitation (legal_city_tiles).
    Returns (coord, rationale) or (None, "") if no legal tile exists.
    """
    if not dd:
        return None, ""
    free_placement = dd.get("free_placement", False)
    tile_req = dd.get("tile_requirement", "land")
    if free_placement:
        tiles = legal_placement.get("legal_city_tiles", [])
    elif tile_req == "water":
        tiles = legal_placement.get("legal_water_tiles", [])
    else:
        tiles = legal_placement.get("legal_land_tiles", [])
    if not tiles:
        return None, ""

    luxury_set = set(_luxury_keys())
    best_coord, best_score, best_rationale = None, -1, ""
    for tile_info in tiles:
        coord, score, rationale = _score_tile(tile_info, key, need_weights, prices, luxury_set)
        if coord and (best_coord is None or score > best_score):
            best_coord, best_score, best_rationale = coord, score, rationale
    if best_coord is None:
        # No tile offers a node/range bonus — still need somewhere to build.
        best_coord = tiles[0]["coord"]
    return best_coord, best_rationale


def _claim_district_tile(nation_name, district_id, def_key, display_name, coord, dry_run=False):
    """Write the district claim onto the chosen hex tile in the DB so the
    placement is real (visible on the map, eligible for node/terrain
    modifiers). Returns the tile's node resource key, or "" if none/no tile.

    dry_run=True reads the tile's node without writing the claim — used by
    the read-only AI Goals Preview tool so simply viewing a preview never
    mutates the live hex map.
    """
    if not coord or not nation_name:
        return ""
    q, r = coord
    tile = mongo.db.hex_map_tiles.find_one(
        {"q": {"$in": [q, float(q)]}, "r": {"$in": [r, float(r)]}}
    )
    if not tile:
        return ""
    if not dry_run:
        mongo.db.hex_map_tiles.update_one(
            {"_id": tile["_id"]},
            {"$set": {"district": {
                "id": district_id, "def_key": def_key,
                "display_name": display_name, "type": "",
            }}},
        )
    node = tile.get("node") or {}
    if isinstance(node, dict):
        node_key = node.get("resource_type") or node.get("value") or node.get("resource") or ""
    else:
        node_key = str(node) if node else ""
    return node_key if node_key != "none" else ""


def _claim_city_tile(nation_name, city_id, city_type, coord, set_capital=False, dry_run=False):
    """Write the city onto the chosen hex tile so it appears on the map.

    Mirrors _claim_district_tile. dry_run=True reads without writing —
    used by the read-only AI Goals Preview tool.
    Returns the tile's node resource key, or "" if none.
    """
    if not coord or not nation_name:
        return ""
    q, r = coord
    tile = mongo.db.hex_map_tiles.find_one(
        {"q": {"$in": [q, float(q)]}, "r": {"$in": [r, float(r)]}}
    )
    if not tile:
        return ""
    if not dry_run:
        update = {"city": {"id": city_id, "name": "", "type": city_type}}
        if set_capital:
            update["capital"] = True
        mongo.db.hex_map_tiles.update_one({"_id": tile["_id"]}, {"$set": update})
    node = tile.get("node") or {}
    if isinstance(node, dict):
        node_key = node.get("resource_type") or node.get("value") or node.get("resource") or ""
    else:
        node_key = str(node) if node else ""
    return node_key if node_key != "none" else ""


def _select_best_city(old_nation, state, exclude_types=None):
    """
    For grow_population: pick the best city to build (or replace).
    Evaluates conditional modifiers against the nation's current state to find
    the city type that would give the most effective_pop_capacity.
    Also scores the best tile placement for node and metropolis bonuses.
    exclude_types: set of city types built this session (to prevent duplicates).
    Returns a plan dict with source='city', or None if no city avenue exists.
    """
    cities_data = json_data.get("cities", {})
    city_slots = old_nation.get("city_slots", 0)
    existing_cities = old_nation.get("cities", [])
    open_city_slots = max(0, city_slots - len(existing_cities))
    if exclude_types:
        open_city_slots = max(0, open_city_slots - len(exclude_types))

    if city_slots <= 0:
        return None

    # Score each city type by total pop cap including conditional bonuses.
    # At equal pop cap, prefer cheaper cities (lower total resource cost).
    # Exclude city types the nation already has or built this session (max 1 of each),
    # except generic cities which can be built multiple times.
    existing_city_types = {c.get("type", "") for c in existing_cities}
    if exclude_types:
        existing_city_types = existing_city_types | exclude_types
    existing_city_types.discard("generic")
    nation_researched = {k for k, v in old_nation.get("technologies", {}).items() if v.get("researched")}
    city_scores = []
    for ct, cdata in cities_data.items():
        if ct in existing_city_types:
            continue
        req_tech = cdata.get("requirements", {}).get("tech")
        if req_tech and req_tech not in nation_researched:
            continue
        total_cap = _estimate_city_pop_cap(ct, cities_data, old_nation)
        cost = dict(cdata.get("cost", {}))
        total_cost = sum(cost.values())
        city_scores.append((ct, cdata.get("display_name", ct), total_cap, cost, total_cost))

    if not city_scores and open_city_slots <= 0:
        return None

    city_scores.sort(key=lambda x: (x[2], -x[4]), reverse=True)

    if open_city_slots > 0 and city_scores:
        from calculations.field_calculations import _compute_legal_placement
        legal = _compute_legal_placement(old_nation)
        prices = _base_prices()
        baseline_w = _weights_from_net(
            state["net_production"], state["stockpiles"], prices, state["money_income"],
            active_resources=state.get("active_resources"), money_stock=state.get("money"),
        )

        best = city_scores[0]
        has_city = len(existing_cities) > 0 and any(c.get("type") for c in existing_cities)
        capital_coord = legal.get("capital_coord")

        if not has_city:
            # First city must be placed on the capital.
            # If no capital exists, pick the best tile as both capital and city site.
            if capital_coord:
                tile_coord = capital_coord
                tile_rationale = "capital (first city)"
            else:
                tile_coord, _, tile_rationale = _score_best_city_tile(
                    legal, best[0], baseline_w, prices
                )
                tile_rationale = f"auto-capital: {tile_rationale}" if tile_rationale else "auto-capital"
        else:
            tile_coord, _, tile_rationale = _score_best_city_tile(
                legal, best[0], baseline_w, prices
            )

        placement_info = ""
        if tile_coord:
            placement_info = f" tile ({tile_coord[0]},{tile_coord[1]})"
            if tile_rationale:
                placement_info += f" [{tile_rationale}]"

        plan = {
            "key": best[0],
            "display_name": f"City: {best[1]}",
            "cost": best[3],
            "rationale": f"Build new city (+{best[2]} pop cap), {open_city_slots} slot(s) open{placement_info}",
            "sessions_saving": 0,
            "source": "city",
        }
        if tile_coord:
            plan["placement"] = {"q": tile_coord[0], "r": tile_coord[1]}
            if not capital_coord:
                plan["set_capital"] = True
        return plan

    # No open slots or no new types available — check if we can replace
    # the weakest existing city with a better type not already owned
    existing_caps = []
    for c in existing_cities:
        ct = c.get("type", "")
        cap = _estimate_city_pop_cap(ct, cities_data, old_nation)
        existing_caps.append((ct, cap, c.get("name", ct)))

    if not existing_caps:
        return None

    worst_type, worst_cap, worst_name = min(existing_caps, key=lambda x: x[1])
    for ct, display, new_cap, cost, _ in city_scores:
        if new_cap > worst_cap:
            return {
                "key": ct,
                "display_name": f"City: {display} (replace {worst_name})",
                "cost": cost,
                "rationale": f"Replace {worst_type} (+{worst_cap}) with {ct} (+{new_cap} pop cap)",
                "sessions_saving": 0,
                "source": "city",
            }

    return None


def sync_nation_cities(nation, dry_run=True, tiles_with_city=None, owned_tiles=None):
    """
    Reconcile a nation's `cities` array against city objects placed on its
    owned map tiles. Two one-way operations, never destructive:

    1. Map → Nation: any tile owned by this nation with a `city` object whose
       id isn't in nation.cities gets appended to nation.cities.
    2. Nation → Map: any nation.cities entry with a real type set (blank
       placeholder slots are skipped) whose id isn't placed on any owned
       tile gets placed using the same tile-scoring logic the AI uses when
       building a new city (_score_best_city_tile / capital fallback).

    Existing cities/tiles are never modified or removed.

    tiles_with_city: optional pre-fetched list of this nation's own tiles that
    have a `city` set ({"q","r","city"} projection). Callers syncing many
    nations in one pass should batch-fetch all such tiles in a single query
    and pass the per-nation slice here instead of letting every call hit the
    DB — that N+1 query pattern is what was timing out on production.

    owned_tiles: optional pre-fetched list of ALL of this nation's tiles
    (passed straight through to _compute_legal_placement), same reasoning —
    avoids a second per-nation query for the placement scan.

    Returns {
        "name": str,
        "added_to_nation": [{"id","name","type"}, ...],
        "placed_on_map": [{"id","name","type","coord":[q,r],"rationale"}, ...],
        "unplaceable": [{"id","name","type"}, ...],
    }
    When dry_run is False, performs the DB writes for both directions.
    """
    nation_name = nation.get("name", "")
    nation_cities = nation.get("cities", []) or []
    nation_city_ids = {c.get("_id") for c in nation_cities if c.get("_id")}

    if tiles_with_city is None:
        tiles_with_city = list(mongo.db.hex_map_tiles.find(
            {"owner": nation_name, "city": {"$exists": True, "$ne": None}},
            {"q": 1, "r": 1, "city": 1},
        )) if nation_name else []
    tile_city_ids = {
        t["city"]["id"]: t for t in tiles_with_city
        if isinstance(t.get("city"), dict) and t["city"].get("id")
    }

    report = {
        "name": nation_name,
        "added_to_nation": [],
        "placed_on_map": [],
        "unplaceable": [],
    }

    # --- Direction 1: Map → Nation ---
    # Fill existing blank placeholder slots first (preserving each new city's
    # tile-linked _id), only appending past the end of the array once those
    # are used up — otherwise new cities land past city_slots and get cut
    # off the next time the page is edited.
    blank_indices = [
        i for i, c in enumerate(nation_cities)
        if isinstance(c, dict) and not c.get("type")
    ]
    set_ops = {}
    push_entries = []
    blank_cursor = 0
    for cid, tile in tile_city_ids.items():
        if cid in nation_city_ids:
            continue
        city_data = tile["city"]
        entry = {
            "_id": cid,
            "name": city_data.get("name", ""),
            "type": city_data.get("type", ""),
            "node": "",
            "wall": "",
        }
        if blank_cursor < len(blank_indices):
            idx = blank_indices[blank_cursor]
            blank_cursor += 1
            for field, value in entry.items():
                set_ops[f"cities.{idx}.{field}"] = value
        else:
            push_entries.append(entry)
        report["added_to_nation"].append({
            "id": cid, "name": entry["name"], "type": entry["type"],
        })

    if not dry_run and (set_ops or push_entries):
        update_doc = {}
        if set_ops:
            update_doc["$set"] = set_ops
        if push_entries:
            update_doc["$push"] = {"cities": {"$each": push_entries}}
        mongo.db.nations.update_one({"_id": nation["_id"]}, update_doc)

    # --- Direction 2: Nation → Map ---
    # Skip blank placeholder entries (no type set) — these are unused city
    # slots, not real cities, and should never be placed on the map.
    # Nomadic nations are skipped entirely: their cities live on the nation
    # doc only and must never be placed on map tiles.
    to_place = [
        c for c in nation_cities
        if c.get("_id") and c["_id"] not in tile_city_ids and c.get("type")
    ]
    if to_place and _nation_is_nomadic(nation):
        report["skipped_nomadic"] = [
            {"id": c["_id"], "name": c.get("name", ""), "type": c.get("type", "")}
            for c in to_place
        ]
        to_place = []
    if to_place and nation_name:
        from calculations.field_calculations import _compute_legal_placement
        prices = _base_prices()
        state = evaluate_nation_state(nation)
        baseline_w = _weights_from_net(
            state["net_production"], state["stockpiles"], prices, state["money_income"],
            active_resources=state.get("active_resources"), money_stock=state.get("money"),
        )
        has_city_on_map = bool(tile_city_ids)

        # Computed once per nation, not once per city — this is the single
        # DB query (+ admin-range scan) for the whole placement pass. Tiles
        # claimed earlier in this loop are filtered out via reserved_coords
        # below rather than by re-querying, since nothing in the DB needs to
        # change for that exclusion to be correct (dry-run or not).
        nation.pop("_legal_placement_cache", None)
        legal_base = _compute_legal_placement(nation, owned_tiles=owned_tiles)
        reserved_coords = set()

        for c in to_place:
            legal = legal_base
            if reserved_coords:
                legal = dict(legal_base)
                legal["legal_city_tiles"] = [
                    t for t in legal_base.get("legal_city_tiles", [])
                    if t["coord"] not in reserved_coords
                ]
            city_type = c.get("type", "generic")

            if not has_city_on_map:
                capital_coord = legal.get("capital_coord")
                if capital_coord:
                    coord, rationale = capital_coord, "capital (first city)"
                else:
                    coord, _, rationale = _score_best_city_tile(legal, city_type, baseline_w, prices)
                    rationale = f"auto-capital: {rationale}" if rationale else "auto-capital"
            else:
                coord, _, rationale = _score_best_city_tile(legal, city_type, baseline_w, prices)

            if not coord:
                report["unplaceable"].append({
                    "id": c["_id"], "name": c.get("name", ""), "type": city_type,
                })
                continue

            reserved_coords.add(tuple(coord))
            report["placed_on_map"].append({
                "id": c["_id"], "name": c.get("name", ""), "type": city_type,
                "coord": [coord[0], coord[1]], "rationale": rationale,
            })

            if not dry_run:
                tile_update = {"city": {"id": c["_id"], "name": c.get("name", ""), "type": city_type}}
                if not has_city_on_map and not legal.get("capital_coord"):
                    tile_update["capital"] = True
                mongo.db.hex_map_tiles.update_one(
                    {"q": coord[0], "r": coord[1]},
                    {"$set": tile_update},
                )

            has_city_on_map = True

    return report


def _district_category(dk):
    """Category of a district def, via the per-tick cache when loaded."""
    global _DISTRICT_DEFS_CACHE
    if _DISTRICT_DEFS_CACHE is not None:
        for dd in _DISTRICT_DEFS_CACHE:
            if dd.get("key") == dk:
                return dd.get("category", "")
        return ""
    try:
        dd_doc = mongo.db.district_defs.find_one({"key": dk}, {"category": 1, "_id": 0})
        return (dd_doc or {}).get("category", "")
    except Exception:
        return ""


def _apply_goal_alignment(scored, goal_type, state=None):
    """Apply goal-alignment bonus multiplier to scored districts.
    For "dynamic" goals (e.g. stabilize_economy), goal_job_resources is computed
    from the nation's current baseline deficits rather than a fixed list."""
    affinity = GOAL_DISTRICT_AFFINITY.get(goal_type, {})
    bonus_mult = affinity.get("bonus", 1.0)
    goal_categories = set(affinity.get("categories", []))
    goal_job_resources = set(affinity.get("job_resources", []))
    goal_modifier_fields = set(affinity.get("modifier_fields", []))
    goal_def_keys = set(affinity.get("def_keys", []))
    if affinity.get("dynamic"):
        if state is not None:
            goal_job_resources = {
                r for r, net in state.get("net_production", {}).items() if net < 0
            }
        else:
            return scored, goal_categories, goal_job_resources, goal_modifier_fields

    def _beneficial(field, val):
        return val > 0 or (field in NEGATIVE_IS_GOOD and val < 0)

    adjusted = []
    for entry in scored:
        base_score, dk, display_name = entry[0], entry[1], entry[2]
        cost_d, rationale, source = entry[3], entry[4], entry[5]
        mod_bd = entry[6] if len(entry) > 6 else []
        uj = entry[7] if len(entry) > 7 else []
        g_match = False
        g_reason = ""
        if goal_def_keys and dk in goal_def_keys:
            g_match = True
            g_reason = f"goal district '{dk}'"
        if not g_match and goal_categories:
            category = _district_category(dk)
            if category in goal_categories:
                g_match = True
                g_reason = f"category '{category}'"
        if not g_match and uj:
            for j in uj:
                for p in j.get("production", []):
                    if p.get("field", "") in goal_job_resources:
                        g_match = True
                        g_reason = f"unlocks {j['name']} producing {p['field']}"
                        break
                if g_match:
                    break
        if not g_match and goal_modifier_fields and mod_bd:
            for field, val, *_ in mod_bd:
                if field in goal_modifier_fields and _beneficial(field, val):
                    g_match = True
                    g_reason = f"modifier {field}"
                    break
        if not g_match and goal_job_resources and mod_bd:
            for field, val, *_ in mod_bd:
                if _beneficial(field, val):
                    res_key = field
                    if field.endswith("_production"):
                        res_key = field[:-len("_production")]
                    if res_key in goal_job_resources:
                        g_match = True
                        g_reason = f"produces {res_key}"
                        break
        # Only boost positive scores — amplifying a negative score would make
        # goal-relevant districts look WORSE in the debug list.
        fs = base_score * bonus_mult if (g_match and base_score > 0) else base_score
        gt = f" [GOAL: {g_reason} ×{bonus_mult}]" if g_match else ""
        adjusted.append((fs, dk, display_name, cost_d, rationale + gt, source, mod_bd, uj))
    adjusted.sort(key=lambda x: x[0], reverse=True)
    return adjusted, goal_categories, goal_job_resources, goal_modifier_fields


def _district_matches_goal(dd, goal_type, state=None):
    """Whether a district def is relevant to a goal's affinity — by def_key,
    category, or a beneficial modifier in the goal's fields/resources.
    Works from the raw district def (no scoring needed)."""
    affinity = GOAL_DISTRICT_AFFINITY.get(goal_type, {})
    if not affinity:
        return False
    if dd.get("key", "") in set(affinity.get("def_keys", [])):
        return True
    if dd.get("category", "") in set(affinity.get("categories", [])):
        return True
    goal_fields = set(affinity.get("modifier_fields", [])) | set(affinity.get("job_resources", []))
    if affinity.get("dynamic") and state is not None:
        goal_fields |= {r for r, net in state.get("net_production", {}).items() if net < 0}
    if not goal_fields:
        return False
    for m in dd.get("modifiers", []):
        field = _try_resolve_field(m)
        val = m.get("value", 0)
        if not isinstance(val, (int, float)):
            continue
        if not (val > 0 or (field in NEGATIVE_IS_GOOD and val < 0)):
            continue
        res_key = field[: -len("_production")] if field.endswith("_production") else field
        if field in goal_fields or res_key in goal_fields:
            return True
    return False


def _future_resource_utility(state, secondary_goal=None, old_nation=None):
    """
    How useful each resource will be for FUTURE construction: aggregate the
    build costs of every district the nation could still build and normalize
    to per-resource shares (summing to 1). Districts matching the secondary
    goal's affinity count double — "produce what the next goal will need".
    Drives grow_economy's resource weighting instead of a hardcoded list.
    """
    global _DISTRICT_DEFS_CACHE
    if _DISTRICT_DEFS_CACHE is not None:
        defs = _DISTRICT_DEFS_CACHE
    else:
        try:
            defs = list(mongo.db.district_defs.find({}, {"_id": 0}))
        except Exception:
            defs = []

    check_reqs = None
    if old_nation is not None:
        from calculations.field_calculations import check_district_requirements
        check_reqs = check_district_requirements

    secondary_type = (secondary_goal or {}).get("type", "")
    totals = {}
    for dd in defs:
        dk = dd.get("key", "")
        if not dk:
            continue
        existing = state.get("existing_def_key_counts", {}).get(dk, 0)
        if existing >= dd.get("map_count", 1):
            continue
        if check_reqs is not None and not check_reqs(old_nation, dd):
            continue
        mult = 2.0 if secondary_type and _district_matches_goal(dd, secondary_type, state) else 1.0
        for r, amt in dd.get("cost", {}).items():
            if isinstance(amt, (int, float)) and amt > 0:
                if r == "money":
                    # Money costs run 250-2000 vs 2-20 for resources; convert to
                    # resource-equivalent units (base price 50) so shares compare.
                    amt = amt / 50.0
                totals[r] = totals.get(r, 0) + amt * mult
    total = sum(totals.values())
    if total <= 0:
        return {}
    return {r: round(v / total, 4) for r, v in totals.items()}


def _goal_adjusted_need_weights(need_weights, goal_type, state=None):
    """Apply goal-specific weight adjustments so district scoring values what the goal needs.
    Stability fields need very high weights because their production amounts are small
    (0.10-0.35) and they have price_scale 1.0 (vs 2.0-4.0 for real resources),
    so they need proportionally larger weights to compete in district scoring."""
    w = dict(need_weights)
    if goal_type == "stabilize_nation":
        # TODO: law changes are the primary lever for this goal — not yet implemented
        w["stability_gain_chance"] = w.get("stability_gain_chance", 0.6) + 60.0
        w["stability_loss_chance"] = w.get("stability_loss_chance", -0.8) - 45.0
        w["max_stability_gain_chance"] = w.get("max_stability_gain_chance", 0.6) + 40.0
    elif goal_type == "prepare_war":
        for r in ("iron", "gunpowder"):
            w[r] = w.get(r, 1.3) + 2.0
    elif goal_type == "develop_technology":
        w["research"] = w.get("research", 0.4) + 3.0
    elif goal_type == "stabilize_economy" and state is not None:
        # The point of this goal is keeping the upkeep share of the workforce
        # low enough that spare pops exist for districts/units. Two tiers:
        # (a) deficits jobs could NOT cover — genuine survival, dramatic boost;
        # (b) resources with pops pinned to sustaining them — boost scales with
        #     how much of the workforce they consume (9/10 pops on hunters →
        #     food districts dominate), modest to avoid double-counting the
        #     upkeep-burden credit in score_buildable_districts.
        unresolved = state.get("unresolved_deficits") or set()
        for r in unresolved:
            w[r] = w.get(r, 1.3) * 4.0 + 2.0
        burden = state.get("upkeep_burden") or {}
        total_pops = max(1, state.get("total_pops", 1))
        for r, pinned in burden.items():
            if r in unresolved:
                continue
            w[r] = w.get(r, 0.8) * (1.0 + 2.0 * pinned / total_pops)
    elif goal_type == "grow_economy" and state is not None:
        # Weight resources by how useful they'll be for FUTURE construction
        # (aggregate district build costs, secondary-goal districts doubled)
        # instead of a hardcoded staples list.
        utility = state.get("future_utility")
        if utility is None:
            utility = _future_resource_utility(state, state.get("secondary_goal"))
        for r, share in utility.items():
            if r == "money":
                continue
            w[r] = w.get(r, 0.3) + share * 10.0
    return w


def evaluate_goal_district(old_nation, new_nation, state, goal, need_weights, prices, upkeep_assignments, log, dry_run=False):
    """
    Build affordable districts and cities in a loop. After each build:
    - Re-evaluate upkeep assignments (new districts may unlock better jobs)
    - Re-evaluate the strategic goal (building a city may resolve pop cap)
    - Re-score remaining districts with updated weights and goal alignment

    dry_run=True simulates tile placement (for the decision log) without
    writing the claim to hex_map_tiles — used by the read-only AI Goals
    Preview tool so a preview render never mutates the live hex map.
    Returns (district_plan, district_scores, district_log, upkeep_assignments, goal).
    """
    from calculations.field_calculations import check_job_requirements

    district_log = []
    district_plan = None
    max_builds = 5
    built_count = 0

    # Nomads build districts/cities on the nation doc but never claim map tiles.
    is_nomadic = _nation_is_nomadic(old_nation)

    # Apply goal-specific weight adjustments so district scoring
    # properly values goal-relevant modifiers (e.g. stability for stabilize_nation)
    scoring_weights = _goal_adjusted_need_weights(need_weights, goal.get("type", ""), state)

    # Score initial districts with goal alignment
    scored = score_buildable_districts(old_nation, state, scoring_weights, prices, upkeep_assignments)
    adjusted, _, _, _ = _apply_goal_alignment(scored, goal.get("type", ""), state) if scored else ([], set(), set(), set())

    # Log initial scores for debugging — includes negative-scored candidates
    # (kept in results, never built) so losing districts are explainable.
    district_log.append(f"[initial goal: {goal.get('type', '?')}]")
    for entry in adjusted[:10]:
        district_log.append(f"[initial] {entry[2]}: {entry[0]:.2f} — {entry[4]}")

    personality = get_ai_personality(old_nation)
    built_city_types = set()

    # Count def_keys already claimed on map tiles (safety net against out-of-sync
    # nation docs that miss a previous tile claim — prevents exceeding map_count).
    map_claimed_counts = {}  # {def_key: count_on_map}
    if not dry_run:
        nation_name = old_nation.get("name", "")
        for t in mongo.db.hex_map_tiles.find(
            {"owner": nation_name, "district": {"$exists": True, "$ne": None}},
            {"district.def_key": 1, "_id": 0}
        ):
            dk = (t.get("district") or {}).get("def_key", "")
            if dk:
                map_claimed_counts[dk] = map_claimed_counts.get(dk, 0) + 1

    # Cache map_count limits from district_defs for multi-instance checks
    _map_count_cache = {}  # {def_key: map_count}

    while built_count < max_builds:
        built_this_round = False

        # --- Try city if goal is grow_population ---
        if goal.get("type") == "grow_population":
            city_plan = _select_best_city(old_nation, state, exclude_types=built_city_types)
            if city_plan:
                cost = city_plan.get("cost", {})
                money_ok = state["money"] >= cost.get("money", 0)
                res_ok = all(
                    state["stockpiles"].get(r, 0) >= amt
                    for r, amt in cost.items() if r != "money"
                )
                if money_ok and res_ok:
                    # Deduct resources
                    for r, amt in cost.items():
                        if r == "money":
                            new_nation["money"] = new_nation.get("money", old_nation.get("money", 0)) - amt
                            state["money"] -= amt
                        else:
                            storage = dict(new_nation.get("resource_storage", deepcopy(old_nation.get("resource_storage", {}))))
                            storage[r] = storage.get(r, 0) - amt
                            state["stockpiles"][r] = state["stockpiles"].get(r, 0) - amt
                            new_nation["resource_storage"] = storage

                    # Create the city entry and place it on the map tile.
                    # Nomads never place cities on the map — the city lives
                    # on the nation doc only.
                    import uuid
                    city_id = uuid.uuid4().hex[:8]
                    city_type = city_plan.get("key", "generic")
                    if is_nomadic:
                        coord, node_key = None, ""
                    else:
                        placement = city_plan.get("placement")
                        coord = (placement["q"], placement["r"]) if placement else None
                        node_key = _claim_city_tile(
                            old_nation.get("name", ""), city_id, city_type,
                            coord, set_capital=city_plan.get("set_capital", False),
                            dry_run=dry_run,
                        )
                    city_entry = {
                        "_id": city_id,
                        "name": "",
                        "type": city_type,
                        "node": node_key,
                        "wall": "",
                    }
                    cities = list(new_nation.get("cities", deepcopy(old_nation.get("cities", []))))
                    _insert_filling_empty_slot(
                        cities, city_entry,
                        lambda c: not c.get("type"),
                    )
                    new_nation["cities"] = cities
                    if coord:
                        placed_desc = "Previewed placement" if dry_run else "Placed"
                        district_log.append(f"  {placed_desc} at ({coord[0]},{coord[1]})" + (f" — node: {node_key}" if node_key else ""))

                    built_city_types.add(city_plan.get("key", ""))
                    district_log.append(f"Built city: {city_plan['display_name']}")
                    built_count += 1
                    built_this_round = True

                    # Re-evaluate goal — building a city may resolve pop cap
                    upkeep_assignments, _, _, _, upkeep_ratio, _ = compute_upkeep_floor(state, prices)
                    goal, _ = select_strategic_goal(
                        old_nation, state, personality, upkeep_ratio, prices
                    )
                    district_log.append(f"  Re-evaluated goal: {goal['display_name']} (score {goal['score']})")
                else:
                    # Can't afford city — set as plan
                    current_plan = old_nation.get("ai_state", {}).get("planned_district")
                    if current_plan and current_plan.get("source") == "city" and current_plan.get("key") == city_plan.get("key"):
                        city_plan["sessions_saving"] = current_plan.get("sessions_saving", 0) + 1
                    district_plan = city_plan
                    district_log.append(f"Saving for city: {city_plan['display_name']} (session {city_plan.get('sessions_saving', 0) + 1})")
                    break

        # --- Try district ---
        if not built_this_round and state["open_district_slots"] > 0:
            if built_count > 0:
                # Re-score with post-upkeep weights + goal adjustments
                _, _, upkeep_projected, _, _, _ = compute_upkeep_floor(state, prices)
                nw_updated = _weights_from_net(
                    upkeep_projected, state["stockpiles"], prices, state["money_income"],
                    state.get("resource_capacity"),
                    active_resources=state.get("active_resources"), money_stock=state.get("money"),
                )
                nw_updated = _goal_adjusted_need_weights(nw_updated, goal.get("type", ""), state)
                scored = score_buildable_districts(old_nation, state, nw_updated, prices, upkeep_assignments)
                adjusted, _, _, _ = _apply_goal_alignment(scored, goal.get("type", ""), state) if scored else ([], set(), set(), set())

            if not adjusted:
                break

            # Pick the best-scoring district (not the best affordable one).
            # If it can be built now, build it. Otherwise save for it and stop.
            best = None
            for candidate in adjusted:
                if candidate[0] <= 0:
                    break
                cdk = candidate[1]
                if cdk not in _map_count_cache:
                    dd_tmp = mongo.db.district_defs.find_one({"key": cdk}, {"map_count": 1, "_id": 0})
                    _map_count_cache[cdk] = (dd_tmp or {}).get("map_count", 1)
                allowed = _map_count_cache[cdk]
                on_map = map_claimed_counts.get(cdk, 0)
                in_nation = state.get("existing_def_key_counts", {}).get(cdk, 0)
                # Use the higher of the two counts (map vs nation) as the guard
                if max(on_map, in_nation) < allowed:
                    best = candidate
                    break

            if not best:
                break

            c_score, c_key, c_name, c_cost, c_rationale, c_source = best[:6]
            money_ok = state["money"] >= c_cost.get("money", 0)
            res_ok = all(
                state["stockpiles"].get(r, 0) >= amt
                for r, amt in c_cost.items() if r != "money"
            )

            if money_ok and res_ok:
                import uuid
                district_id = uuid.uuid4().hex[:8]
                coord, node_key, dd = None, "", None
                if c_source == "db" and not is_nomadic:
                    dd = mongo.db.district_defs.find_one({"key": c_key})
                    if dd:
                        from calculations.field_calculations import _compute_legal_placement
                        legal_for_pick = _compute_legal_placement(old_nation)
                        coord, tile_rationale = _pick_district_tile(
                            legal_for_pick, dd, c_key, scoring_weights, prices
                        )
                        if coord:
                            node_key = _claim_district_tile(
                                old_nation.get("name", ""), district_id, c_key,
                                dd.get("display_name", c_key), coord, dry_run=dry_run
                            )
                new_entry = (
                    {"_id": district_id, "def_key": c_key, "node": node_key, "upgrades": []}
                    if c_source == "db"
                    else {"_id": district_id, "type": c_key, "node": "", "era": 1}
                )
                if coord:
                    placed_desc = "Previewed placement" if dry_run else "Placed"
                    district_log.append(f"  {placed_desc} at ({coord[0]},{coord[1]})" + (f" — node: {node_key}" if node_key else ""))
                    if not dry_run:
                        # Invalidate the cached legal-placement scan so the next
                        # build this session sees this tile as claimed.
                        old_nation.pop("_legal_placement_cache", None)
                        map_claimed_counts[c_key] = map_claimed_counts.get(c_key, 0) + 1
                districts = list(new_nation.get("districts", deepcopy(old_nation.get("districts", []))))
                _insert_filling_empty_slot(
                    districts, new_entry,
                    lambda d: not d.get("def_key") and not d.get("type"),
                )
                new_nation["districts"] = districts

                storage = dict(new_nation.get("resource_storage", deepcopy(old_nation.get("resource_storage", {}))))
                for r, amt in c_cost.items():
                    if r == "money":
                        new_nation["money"] = new_nation.get("money", old_nation.get("money", 0)) - amt
                        state["money"] -= amt
                    else:
                        storage[r] = storage.get(r, 0) - amt
                        state["stockpiles"][r] = state["stockpiles"].get(r, 0) - amt
                new_nation["resource_storage"] = storage

                state["existing_def_keys"].add(c_key)
                counts = state.setdefault("existing_def_key_counts", {})
                counts[c_key] = counts.get(c_key, 0) + 1
                state["open_district_slots"] -= 1

                # Update available jobs
                jobs_data = json_data.get("jobs", {})
                merged_nation = dict(old_nation)
                merged_nation["districts"] = new_nation["districts"]
                for jk, jdata in jobs_data.items():
                    if jk not in state["available_jobs"]:
                        if old_nation.get(f"locks_{jk}", 0):
                            continue
                        if check_job_requirements(merged_nation, jdata, {}):
                            state["available_jobs"][jk] = jdata

                # Apply the new district's job production/upkeep modifiers to
                # the AI's job view immediately (e.g. sawmill's +1 harvester
                # wood). job_details on the doc only refreshes on the next
                # recalc, so without this the AI scores the boosted job at its
                # old value in the same session it built the booster.
                if c_source == "db":
                    if dd is None:
                        dd = mongo.db.district_defs.find_one({"key": c_key}) or {}
                    for m in dd.get("modifiers", []):
                        m_type = m.get("modifier_type", "")
                        m_job = m.get("job", "")
                        m_res = m.get("resource", "")
                        m_val = m.get("value", 0)
                        if (m_type not in ("job_resource_production", "job_resource_upkeep")
                                or not m_job or not m_res
                                or not isinstance(m_val, (int, float))
                                or m_job not in state["available_jobs"]):
                            continue
                        patched = deepcopy(state["available_jobs"][m_job])
                        if m_type == "job_resource_production":
                            prod = patched.setdefault("production", {})
                            prod[m_res] = max(0, prod.get(m_res, 0) + m_val)
                        else:
                            upk = patched.setdefault("upkeep", {})
                            upk[m_res] = max(0, upk.get(m_res, 0) + m_val)
                        state["available_jobs"][m_job] = patched

                # Re-evaluate upkeep and goal
                upkeep_assignments, _, _, _, upkeep_ratio, _ = compute_upkeep_floor(state, prices)
                goal, _ = select_strategic_goal(
                    old_nation, state, personality, upkeep_ratio, prices
                )

                district_log.append(f"Built district: {c_name} (score {c_score:.1f})")
                district_log.append(f"  Re-evaluated goal: {goal['display_name']} (score {goal['score']})")
                built_count += 1
                built_this_round = True
            else:
                # Can't afford the best district — set as plan and stop building
                district_plan = {
                    "key": c_key,
                    "display_name": c_name,
                    "cost": c_cost,
                    "rationale": c_rationale,
                    "sessions_saving": 0,
                    "source": c_source,
                }
                current_plan = old_nation.get("ai_state", {}).get("planned_district")
                if current_plan and current_plan.get("key") == c_key:
                    district_plan["sessions_saving"] = current_plan.get("sessions_saving", 0) + 1
                district_log.append(f"Saving for: {c_name} (session {district_plan['sessions_saving'] + 1}) — {c_rationale}")
                break

        if not built_this_round:
            # No district or city was buildable at all
            break

    if built_count > 0:
        district_log.insert(0, f"Built {built_count} district(s)/city(s) this session")

    log.extend(district_log)
    return district_plan, adjusted, district_log, upkeep_assignments, goal


# ---------------------------------------------------------------------------
# Goal-driven job assignment
# ---------------------------------------------------------------------------

def _compute_goal_resource_needs(goal, district_plan, state, projected_net=None):
    """
    Return a dict of {resource: boost_weight} for resources the goal cares about.
    The boost is additive on top of normal need weights.
    Uses projected_net to determine shortfalls if provided, so that production
    from already-assigned pops is accounted for.
    """
    boosts = {}
    goal_type = goal.get("type", "")

    # If saving for a district/city, boost resources we're short on for its cost
    # and suppress resources we already have enough of
    if district_plan:
        cost = district_plan.get("cost", {})
        for r, amt in cost.items():
            if r == "money":
                continue
            current = state["stockpiles"].get(r, 0)
            # Use projected stockpile including consumption from assigned jobs.
            # Don't clip negative net — if metallurgists are consuming stone,
            # the AI needs to know it won't have enough for the city next session.
            if projected_net:
                projected_stock = current + projected_net.get(r, 0)
            else:
                projected_stock = current
            if projected_stock <= amt:
                deficit_ratio = min(3.0, max(0, amt - projected_stock) / max(amt, 1))
                boosts[r] = boosts.get(r, 0) + max(3.0, 2.0 * deficit_ratio)

    # Goal-specific resource elevations
    if goal_type == "prepare_war":
        for r in ("iron", "gunpowder", "mounts"):
            boosts[r] = boosts.get(r, 0) + 2.0
    elif goal_type == "develop_technology":
        boosts["research"] = boosts.get("research", 0) + 3.0
        boosts["iron"] = boosts.get("iron", 0) + 1.5  # researcher upkeep
    elif goal_type == "expand_trade":
        boosts["money_income"] = boosts.get("money_income", 0) + 2.0
    elif goal_type == "stabilize_economy":
        # Boost what is STILL in deficit given the pops assigned so far, so
        # boosts turn off once a resource reaches excess and spare pops flow
        # to the next need instead of piling onto a solved deficit.
        live_net = projected_net if projected_net is not None else state["net_production"]
        for r, net in live_net.items():
            if net < 0:
                boosts[r] = boosts.get(r, 0) + 2.0
    elif goal_type == "grow_economy":
        # Produce what future construction will need (secondary-goal districts
        # counted double) instead of a hardcoded staples list.
        utility = state.get("future_utility")
        if utility is None:
            utility = _future_resource_utility(state, state.get("secondary_goal"))
        if utility:
            for r, share in utility.items():
                if r == "money":
                    continue
                boosts[r] = boosts.get(r, 0) + share * 8.0
            boosts["money_income"] = boosts.get("money_income", 0) + utility.get("money", 0) * 8.0 + 1.0
        else:
            for r in ("food", "wood", "stone", "iron", "mounts"):
                boosts[r] = boosts.get(r, 0) + 1.0
            boosts["money_income"] = boosts.get("money_income", 0) + 1.5
    elif goal_type == "grow_population":
        boosts["food"] = boosts.get("food", 0) + 1.5

    return boosts


def assign_goal_jobs(state, goal, remaining_pops, projected_net, district_plan, prices=None):
    """
    Assign remaining pops (after upkeep) to jobs that advance the strategic goal.
    Uses dynamic goal-resource-need weights instead of hardcoded job weights.
    Returns (goal_assignments, goal_log, final_projected_net).
    """
    if remaining_pops <= 0:
        return {}, [], dict(projected_net)

    prices = prices if prices is not None else _base_prices()
    projected_net = dict(projected_net)
    assignments = {}
    pops_left = remaining_pops
    goal_type = goal.get("type", "")

    resource_capacity = state.get("resource_capacity", {})

    # Production malus still absorbed by the 0-floor clamp after upkeep jobs
    # (e.g. Savior's -1): the next pop's production of such a resource is
    # partially eaten, so both the job scores and the projection account for it.
    clamp_absorbed = dict(
        state.get("production_clamp_absorbed_remaining",
                  state.get("production_clamp_absorbed")) or {}
    )

    # Stability chance headroom still left after upkeep jobs — once exhausted,
    # more stability_gain_chance/stability_loss_chance production (from
    # brewers, guards, etc.) is wasted because the value is already at its
    # cap/floor. This is exactly what stops the "stabilize_nation" goal's
    # heavy +60/-45 weight boost below from stacking pops onto a stat that
    # can't move any further.
    stability_headroom = dict(
        state.get("stability_chance_headroom_remaining",
                  state.get("stability_chance_headroom")) or {}
    )

    while pops_left > 0:
        # Recompute goal boosts each iteration using projected stockpiles
        # so shortfall detection accounts for production from already-assigned pops
        goal_boosts = _compute_goal_resource_needs(goal, district_plan, state, projected_net)

        # Compute base weights from current projected production
        # Include resource_capacity so capped resources get deprioritized
        base_weights = _weights_from_net(
            projected_net, state["stockpiles"], prices, state["money_income"],
            resource_capacity,
            active_resources=state.get("active_resources"), money_stock=state.get("money"),
        )

        # Apply goal resource boosts
        goal_weights = dict(base_weights)
        for r, boost in goal_boosts.items():
            if r in goal_weights:
                goal_weights[r] = goal_weights.get(r, 1.0) + boost
            elif r in PRODUCTION_FIELD_MAP:
                sk, bw = PRODUCTION_FIELD_MAP[r]
                if sk and sk in goal_weights:
                    goal_weights[sk] = goal_weights.get(sk, 1.0) + boost
                else:
                    goal_weights[r] = bw + boost

        # For stabilize_nation, heavily boost stability fields.
        # These fields have small production amounts (0.05–0.35) and price_scale 1.0
        # (vs 2.0-4.0 for real resources), so weights must be proportionally large.
        if goal_type == "stabilize_nation":
            goal_weights["stability_gain_chance"] = goal_weights.get("stability_gain_chance", 0.6) + 60.0
            goal_weights["stability_loss_chance"] = goal_weights.get("stability_loss_chance", -0.8) - 45.0

        # Reduce weight for resources approaching cap AFTER goal boosts.
        # Excess can be sold, so this is a soft reduction, not a hard floor.
        for r in list(goal_weights.keys()):
            net_r = projected_net.get(r, 0)
            if net_r > 0 and r in resource_capacity:
                cap = resource_capacity[r]
                if cap > 0 and state["stockpiles"].get(r, 0) + net_r >= cap:
                    goal_weights[r] = min(goal_weights[r], max(1.0, goal_weights[r] * 0.2))

        job_scores = score_jobs(
            state, goal_weights, prices,
            clamp_absorbed=clamp_absorbed, stability_headroom=stability_headroom,
        )
        positive = {k: v for k, v in job_scores.items() if v > 0.05}
        if not positive:
            break

        best = max(positive, key=lambda k: positive[k])

        # Check if this job's upkeep would completely deplete a resource
        # that has zero stockpile. Only block truly catastrophic assignments —
        # the weight system already penalizes expensive upkeep through scoring.
        job = state["available_jobs"][best]
        upkeep_ok = True
        for r, amt in job.get("upkeep", {}).items():
            if isinstance(amt, (int, float)) and r in projected_net:
                new_net = projected_net.get(r, 0) - amt
                if new_net < 0 and state["stockpiles"].get(r, 0) <= 0:
                    upkeep_ok = False
                    break

        if not upkeep_ok:
            # Try next best job that doesn't deplete an empty resource
            sorted_jobs = sorted(positive.items(), key=lambda x: -x[1])
            found = False
            for jk, sc in sorted_jobs:
                if jk == best:
                    continue
                j = state["available_jobs"][jk]
                ok = True
                for r, amt in j.get("upkeep", {}).items():
                    if isinstance(amt, (int, float)) and r in projected_net:
                        new_net = projected_net.get(r, 0) - amt
                        if new_net < 0 and state["stockpiles"].get(r, 0) <= 0:
                            ok = False
                            break
                if ok:
                    best = jk
                    found = True
                    break
            if not found:
                break

        assignments[best] = assignments.get(best, 0) + 1
        pops_left -= 1

        job = state["available_jobs"][best]
        for r, amt in job.get("production", {}).items():
            if isinstance(amt, (int, float)) and r in projected_net:
                projected_net[r] = projected_net.get(r, 0) + _consume_clamp_absorbed(clamp_absorbed, r, amt)
            elif isinstance(amt, (int, float)) and r in stability_headroom:
                _consume_headroom(stability_headroom, r, amt)
        for r, amt in job.get("upkeep", {}).items():
            if isinstance(amt, (int, float)) and r in projected_net:
                projected_net[r] = projected_net.get(r, 0) - amt

    log = []
    for jk, cnt in sorted(assignments.items(), key=lambda x: -x[1]):
        if cnt > 0:
            job = state["available_jobs"].get(jk, {})
            log.append(f"Goal ({goal.get('display_name', '?')}): {cnt}× {job.get('display_name', jk)}")
    return {k: v for k, v in assignments.items() if v > 0}, log, projected_net


# ---------------------------------------------------------------------------
# Tech target selection
# ---------------------------------------------------------------------------

TECH_TYPE_WEIGHTS = {
    "Military":       {"aggression": 1.5, "military": 1.5},
    "Food":           {"economic": 1.3},
    "Industry":       {"economic": 1.3},
    "Infrastructure": {"economic": 1.0, "expansion": 1.0},
    "Culture":        {"trade": 1.0, "economic": 0.5},
    "Starting":       {},
    "Capstone":       {"economic": 0.5},
}


def select_tech_target(old_nation, new_nation, state, goal, personality):
    """
    Always pick a tech to invest in if the nation has research production.
    Sets new_nation's investing field. Returns tech_target dict or None.
    """
    research_prod = state["net_production"].get("research", 0)
    # Also check for passive research via resource_excess
    if research_prod <= 0:
        research_prod = old_nation.get("resource_excess", {}).get("research", 0)
    if research_prod <= 0:
        return None

    tech_data = json_data.get("tech", {})
    nation_techs = new_nation.get("technologies", old_nation.get("technologies", {}))
    researched = {k for k, v in nation_techs.items() if v.get("researched")}

    candidates = []
    for tk, tdata in tech_data.items():
        if tk in researched:
            continue
        invested = nation_techs.get(tk, {}).get("invested", 0)
        cost = tdata.get("cost", 1)

        prereqs_all = tdata.get("prerequisites_all", [])
        prereqs_one = tdata.get("prerequisites_one", [])
        prereqs_two = tdata.get("prerequisites_two", [])
        if prereqs_all and not all(p in researched for p in prereqs_all):
            continue
        if prereqs_one and not any(p in researched for p in prereqs_one):
            continue
        if prereqs_two and sum(1 for p in prereqs_two if p in researched) < 2:
            continue

        # Score by type affinity to personality
        tech_type = tdata.get("type", "Culture")
        type_weights = TECH_TYPE_WEIGHTS.get(tech_type, {})
        type_score = 10.0  # base value for any tech
        for dim, mult in type_weights.items():
            type_score += max(0, personality.get(dim, 0)) * mult * 10

        # Prefer cheaper/closer-to-completion techs
        remaining = max(1, cost - invested)
        sessions_to_complete = remaining / max(research_prod, 0.1)
        completion_bonus = max(0, 10 - sessions_to_complete)

        # If goal is develop_technology, general boost
        if goal.get("type") == "develop_technology":
            type_score *= 1.3

        final_score = type_score + completion_bonus
        candidates.append({
            "key": tk,
            "display_name": tdata.get("display_name", tk),
            "type": tech_type,
            "cost": cost,
            "invested": invested,
            "remaining": remaining,
            "sessions_to_complete": round(sessions_to_complete, 1),
            "score": round(final_score, 1),
        })

    if not candidates:
        return None

    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    # Set investing on the nation
    techs = dict(nation_techs)
    if best["key"] not in techs:
        techs[best["key"]] = {"researched": False, "invested": 0, "investing": 0, "cost": best["cost"]}
    techs[best["key"]]["investing"] = research_prod
    new_nation["technologies"] = techs

    return {
        "key": best["key"],
        "display_name": best["display_name"],
        "type": best["type"],
        "score": best["score"],
        "sessions_to_complete": best["sessions_to_complete"],
        "rationale": f"{best['type']} tech, {best['remaining']} research remaining (~{best['sessions_to_complete']} sessions)",
    }


# ---------------------------------------------------------------------------
# Goal-aware trade desires
# ---------------------------------------------------------------------------

def generate_goal_trade_desires(state, goal, personality, district_plan, projected_net, prices=None, old_nation_ref=None, upkeep_projected_net=None, unresolved_deficits=None):
    """
    Build trade desires informed by the strategic goal.
    Each desire gets a source tag: 'survival', 'goal', or 'opportunistic'.
    old_nation_ref: the nation document, used to access money_capacity.
    upkeep_projected_net: post-upkeep production (before goal jobs).
    unresolved_deficits: set of resources the upkeep floor couldn't fix
        (ran out of pops or no available job). Only these trigger survival buys.
    """
    prices = prices if prices is not None else _base_prices()
    common = _general_and_unique_keys()
    luxury = _luxury_keys()
    import_slots = max(0, state.get("remaining_import_slots", 0))
    export_slots = max(0, state.get("remaining_export_slots", 0))
    RESOURCES_PER_SLOT = 4
    import_capacity = import_slots * RESOURCES_PER_SLOT
    export_capacity = export_slots * RESOURCES_PER_SLOT
    goal_type = goal.get("type", "")
    sell_bias = 1.0 + 0.3 * personality.get("trade", 0)

    # Determine goal-relevant resources (to protect from selling)
    # Only include resources the nation actually needs for its plan, not broad categories
    goal_resources = set()
    if goal_type == "prepare_war":
        goal_resources = {"iron", "gunpowder", "mounts"}
    elif goal_type == "develop_technology":
        goal_resources = {"research", "iron"}
    elif goal_type == "stabilize_economy":
        goal_resources = {r for r, net in projected_net.items() if net < 0}
    if district_plan:
        for r in district_plan.get("cost", {}):
            if r != "money":
                goal_resources.add(r)

    buy_candidates = []
    sell_candidates = []

    # Money health: ratio of projected money to capacity (0.0 = broke, 1.0 = full)
    money_cap = old_nation_ref.get("money_capacity", 0) if old_nation_ref else 0
    projected_money = state["money"] + state["money_income"]
    money_ratio = projected_money / money_cap if money_cap > 0 else 0.5

    # Money crisis: negative income with the treasury running dry soon.
    if state["money_income"] < 0:
        money_sessions = state["money"] / -state["money_income"] if state["money"] > 0 else 0.0
    else:
        money_sessions = float("inf")
    money_crisis = money_sessions < 4

    for r in common + luxury:
        # Use projected_net (post-job-assignment) to determine actual deficits
        net = projected_net.get(r, state["net_production"].get(r, 0))
        stockpile = state["stockpiles"].get(r, 0)
        if net < 0 and stockpile > 0:
            sessions = stockpile / (-net)
        elif net < 0:
            sessions = 0.0
        else:
            sessions = float("inf")
        mkt = prices.get(r, 10)
        is_luxury = r in luxury

        # --- Survival buy: only for resources the upkeep floor couldn't fix ---
        # If upkeep deliberately stopped (buffer was sufficient), no buy needed.
        # Only buy when upkeep ran out of pops or no job could produce the resource.
        if unresolved_deficits and r in unresolved_deficits:
            upkeep_net = (upkeep_projected_net or projected_net).get(r, state["net_production"].get(r, 0))
            if upkeep_net < 0 and stockpile > 0:
                survival_sessions = stockpile / (-upkeep_net)
            elif upkeep_net < 0:
                survival_sessions = 0.0
            else:
                survival_sessions = float("inf")
        else:
            survival_sessions = float("inf")

        if survival_sessions < 5:
            urgency = max(0.0, 1.0 - survival_sessions / 5.0)
            mult = 1.05 + urgency * 0.15
            price = int(round(mkt * mult / 5)) * 5
            qty = 1 if is_luxury else min(5, max(1, int(abs(upkeep_net) * 2 + 1)))
            ttype = "Need to Buy" if urgency > 0.5 else "Desire to Buy"
            buy_candidates.append({
                "resource": r, "trade_type": ttype, "price": price,
                "quantity": qty, "_urgency": urgency + 10, "_source": "survival",
            })

        # --- District/city cost buy: resources needed to build planned target ---
        # Only buy if production won't cover the shortfall by next session
        if district_plan and r in district_plan.get("cost", {}) and r != "money":
            needed = district_plan["cost"][r]
            next_session_stockpile = stockpile + net
            if next_session_stockpile < needed:
                shortfall = needed - next_session_stockpile
                price = int(round(mkt * 1.10 / 5)) * 5
                qty = min(5, max(1, int(shortfall)))
                buy_candidates.append({
                    "resource": r, "trade_type": "Desire to Buy", "price": price,
                    "quantity": qty, "_urgency": 7, "_source": "goal",
                })

        cap = state.get("resource_capacity", {}).get(r, 0)

        # --- Stockpile investment: buy low resources when flush with money ---
        # Only if money stockpile will be above 75% capacity after production,
        # and the resource will be below 50% capacity after production
        if not is_luxury and cap > 0:
            money_cap = old_nation_ref.get("money_capacity", 0) if old_nation_ref else 0
            projected_money = state["money"] + state["money_income"]
            projected_stockpile = stockpile + max(0, net)
            if (money_cap > 0 and projected_money > money_cap * 0.75
                    and projected_stockpile < cap * 0.5):
                fill_amount = int(cap * 0.5 - projected_stockpile)
                if fill_amount > 0:
                    buy_price = int(round(mkt * 1.0 / 5)) * 5
                    qty = min(4, max(1, fill_amount))
                    buy_candidates.append({
                        "resource": r, "trade_type": "Desire to Buy", "price": buy_price,
                        "quantity": qty, "_urgency": 2, "_source": "opportunistic",
                    })

        # --- Sell: overflow resources (would exceed stockpile cap) ---
        if not is_luxury and net > 0 and cap > 0 and stockpile + net > cap:
            overflow = int(stockpile + net - cap)
            if overflow > 0 and r not in goal_resources:
                sell_price = int(round(mkt * 0.85 * sell_bias / 5)) * 5
                sell_price = max(sell_price, int(mkt * 0.70 / 5) * 5)
                qty = min(5, max(1, overflow))
                sell_candidates.append({
                    "resource": r, "trade_type": "Desire to Sell", "price": sell_price,
                    "quantity": qty, "_urgency": 20, "_source": "opportunistic",
                })

        # --- Sell: surplus resources (based on post-assignment production) ---
        # Sell thresholds loosen as money drops — a nation low on money should
        # sell off resources it's not actively using to maintain a cash reserve.
        # In a money crisis, positive production FLOW is sellable no matter how
        # thin the stockpile is (producing +7/session and selling 5 of it is
        # sustainable), and a dire crisis (< 2 sessions of gold) overrides even
        # district-plan cost protection — cash first, construction later.
        goal_protected = r in goal_resources and not (money_crisis and money_sessions < 2)
        if not is_luxury and not goal_protected and sessions == float("inf") and net >= 0:
            # Stockpile threshold scales with money health:
            # money_ratio >= 0.75: sell only when stockpile > 20 (comfortable)
            # money_ratio ~0.50: sell when stockpile > 10
            # money_ratio ~0.25: sell when stockpile > 5 (desperate for cash)
            health = max(0.0, money_ratio)
            sell_threshold = max(3, int(20 * min(1.0, health / 0.75)))
            surplus_threshold = max(3, int(6 * min(1.0, health / 0.75)))
            crisis_flow_sell = money_crisis and net > 0

            if stockpile > sell_threshold or crisis_flow_sell:
                surplus_sessions = stockpile / max(net, 0.01) if net > 0 else 100
                if surplus_sessions > surplus_threshold or crisis_flow_sell:
                    # Price discount increases when money is low (more eager to sell)
                    desperation = max(0.0, 1.0 - health / 0.5)
                    sell_mult = (0.90 - desperation * 0.15) * sell_bias
                    if goal_type == "expand_trade":
                        sell_mult = (0.85 - desperation * 0.10) * sell_bias
                    sell_price = int(round(mkt * sell_mult / 5)) * 5
                    sell_price = max(sell_price, int(mkt * 0.60 / 5) * 5)
                    qty = min(5, max(1, int(net * 1.5) if net > 0 else 1))
                    # Urgency increases when money is low
                    sell_urgency = surplus_sessions + (15 if money_ratio < 0.25 else 5 if money_ratio < 0.5 else 0)
                    ttype = "Need to Sell" if money_ratio < 0.25 or surplus_sessions > 12 else "Desire to Sell"
                    source = "goal" if goal_type == "expand_trade" else "opportunistic"
                    if money_crisis:
                        sell_urgency += 25
                        ttype = "Need to Sell"
                        source = "survival"
                    sell_candidates.append({
                        "resource": r, "trade_type": ttype, "price": sell_price,
                        "quantity": qty, "_urgency": sell_urgency, "_source": source,
                    })

    # Deduplicate buy candidates per resource (keep highest urgency)
    seen_buys = {}
    for entry in buy_candidates:
        r = entry["resource"]
        if r not in seen_buys or entry["_urgency"] > seen_buys[r]["_urgency"]:
            seen_buys[r] = entry
    buy_candidates = sorted(seen_buys.values(), key=lambda x: x["_urgency"], reverse=True)

    # Deduplicate sell candidates per resource (keep highest urgency)
    seen_sells = {}
    for entry in sell_candidates:
        r = entry["resource"]
        if r not in seen_sells or entry["_urgency"] > seen_sells[r]["_urgency"]:
            seen_sells[r] = entry
    sell_candidates = list(seen_sells.values())
    sell_candidates.sort(key=lambda x: x["_urgency"], reverse=True)

    # Cap quantities to slot capacity (each slot carries 4 resources of one type)
    # Buys use import slots, sells use export slots
    # Also cap total buy cost against available money
    desires = []
    import_used = 0
    money_budget = state["money"]
    for entry in buy_candidates:
        remaining_slots = import_capacity - import_used
        if remaining_slots <= 0:
            break
        price = entry["price"]
        qty = min(entry["quantity"], remaining_slots)
        # Trim quantity to what the nation can afford
        if price > 0:
            affordable = int(money_budget / price)
            if affordable <= 0:
                continue
            qty = min(qty, affordable)
        total_cost = qty * price
        clean = {k: v for k, v in entry.items() if not k.startswith("_")}
        clean["source"] = entry.get("_source", "opportunistic")
        clean["quantity"] = qty
        desires.append(clean)
        import_used += qty
        money_budget -= total_cost

    export_used = 0
    for entry in sell_candidates:
        remaining = export_capacity - export_used
        if remaining <= 0:
            break
        clean = {k: v for k, v in entry.items() if not k.startswith("_")}
        clean["source"] = entry.get("_source", "opportunistic")
        clean["quantity"] = min(clean["quantity"], remaining)
        desires.append(clean)
        export_used += clean["quantity"]

    return desires


# ---------------------------------------------------------------------------
# Main AI tick
# ---------------------------------------------------------------------------

def ai_decision_tick(old_nation, new_nation, schema):
    """
    Main per-nation AI tick — goal-first architecture.
    Must run AFTER nation_job_cleanup_tick in NATION_TICK_FUNCTIONS.

    Flow:
      1. Evaluate state, personality, prices
      2. Compute upkeep floor (survival jobs)
      3. Select strategic goal (informed by upkeep burden)
      4. Goal-aware district planning
      5. Goal-driven job assignment for remaining pops
      6. Tech target selection (always, if research exists)
      7. Goal-aware trade desires
      8. Persist ai_state
    """
    if old_nation.get("temperament", "Player") == "Player":
        return ""

    log = []

    try:
        # --- Setup ---
        personality   = get_ai_personality(old_nation)
        state         = evaluate_nation_state(old_nation)
        market_prices = get_stored_market_prices(old_nation)

        # Overlay post-income stockpiles from new_nation so the AI sees
        # resources produced by nation_income_tick earlier this same tick.
        # Sessions-until-empty is recalculated to stay consistent.
        new_storage = new_nation.get("resource_storage") or {}
        for r in list(state["stockpiles"]):
            if r in new_storage:
                state["stockpiles"][r] = new_storage[r]
        state["money"] = new_nation.get("money", state["money"])
        for r, stockpile in state["stockpiles"].items():
            net = state["net_production"].get(r, 0)
            if net < 0 and stockpile > 0:
                state["sessions_until_empty"][r] = stockpile / (-net)
            elif net < 0:
                state["sessions_until_empty"][r] = 0.0
            else:
                state["sessions_until_empty"][r] = float("inf")

        # --- Step 1: Upkeep floor ---
        upkeep_assignments, remaining_pops, projected_net, upkeep_log, upkeep_ratio, _ = \
            compute_upkeep_floor(state, market_prices)
        log.extend(upkeep_log)

        # Compute need weights from POST-UPKEEP production. Don't apply cap penalty
        # here — it's unreliable since goal job upkeep hasn't been assigned yet.
        # Cap enforcement happens dynamically inside assign_goal_jobs where each
        # iteration recalculates weights with the updated projected_net.
        need_weights = _weights_from_net(
            projected_net, state["stockpiles"], market_prices, state["money_income"],
            active_resources=state.get("active_resources"), money_stock=state.get("money"),
        )
        job_scores = score_jobs(
            state, need_weights, market_prices,
            stability_headroom=state.get("stability_chance_headroom_remaining"),
        )

        # --- Step 2: Strategic goal ---
        goal, goal_candidates = select_strategic_goal(
            old_nation, state, personality, upkeep_ratio, market_prices
        )
        initial_goal = dict(goal)
        log.append(f"Strategic goal: {goal['display_name']} (score {goal['score']})")
        log.append(f"  Rationale: {goal['rationale']}")

        # Secondary goal (runner-up candidate) — grow_economy weights resources
        # by what the NEXT goal will need to build. Computed once per nation
        # and stashed on state for _goal_adjusted_need_weights /
        # _compute_goal_resource_needs.
        secondary_goal = next(
            (c for c in goal_candidates
             if c.get("type") != goal.get("type") and c.get("score", 0) > 0),
            None,
        )
        state["secondary_goal"] = secondary_goal
        state["future_utility"] = _future_resource_utility(state, secondary_goal, old_nation)
        if secondary_goal:
            log.append(f"Secondary goal: {secondary_goal['display_name']} (score {secondary_goal['score']})")

        # --- Step 3: Goal-aware district/city building (may build multiple, re-evaluates goal after each) ---
        district_plan, district_scores, district_log, upkeep_assignments, goal = evaluate_goal_district(
            old_nation, new_nation, state, goal, need_weights,
            market_prices, upkeep_assignments, log, dry_run=False
        )

        # Re-compute upkeep floor with final state (districts may have changed available jobs)
        upkeep_assignments, remaining_pops, projected_net, _, upkeep_ratio, unresolved_deficits = \
            compute_upkeep_floor(state, market_prices)

        # --- Step 4: Goal-driven job assignment ---
        goal_assignments, goal_job_log, final_projected_net = assign_goal_jobs(
            state, goal, remaining_pops, projected_net,
            district_plan, market_prices
        )
        log.extend(goal_job_log)

        # Reset all non-static jobs to 0 before writing new assignments so that
        # running AI Decision Tick standalone (without Nation Job Cleanup Tick)
        # doesn't accumulate duplicate assignments.  Static jobs (defined in
        # jobs.json with "static": true) — e.g. revolutionary — are set by
        # other tick functions and must be preserved.
        jobs_data = json_data.get("jobs", {})
        static_job_keys = {k for k, v in jobs_data.items() if v.get("static")}
        reset_jobs = {
            jk: (cnt if jk in static_job_keys else 0)
            for jk, cnt in new_nation.get("jobs", {}).items()
        }

        # Write fresh assignments on top of the zeroed base.
        all_assignments = dict(upkeep_assignments)
        for jk, cnt in goal_assignments.items():
            all_assignments[jk] = all_assignments.get(jk, 0) + cnt
        for jk, cnt in all_assignments.items():
            reset_jobs[jk] = reset_jobs.get(jk, 0) + cnt
        new_nation["jobs"] = reset_jobs

        # --- Step 4b: Tech target ---
        tech_target = select_tech_target(old_nation, new_nation, state, goal, personality)
        if tech_target:
            log.append(f"Tech target: {tech_target['display_name']} ({tech_target['rationale']})")

        # --- Step 5: Goal-aware trade (uses final projected_net including goal jobs) ---
        desires = generate_goal_trade_desires(
            state, goal, personality, district_plan, final_projected_net, market_prices,
            old_nation_ref=old_nation, upkeep_projected_net=projected_net,
            unresolved_deficits=unresolved_deficits
        )
        new_nation["resource_desires"] = desires
        for d in desires:
            src = f" [{d.get('source', '')}]" if d.get("source") else ""
            log.append(f"{d['trade_type']} {d['resource']} ×{d['quantity']} @ {d['price']}{src}")

        # --- Build resource detail snapshot (baseline / post-upkeep / final) ---
        resource_detail = {}
        for r in state.get("net_production", {}):
            baseline_net = state["net_production"].get(r, 0)
            upkeep_net = projected_net.get(r, baseline_net)
            final_net = final_projected_net.get(r, upkeep_net)
            stock = state["stockpiles"].get(r, 0)
            sessions = state["sessions_until_empty"].get(r, float("inf"))
            resource_detail[r] = {
                "baseline_net": round(baseline_net, 2),
                "upkeep_net": round(upkeep_net, 2),
                "final_net": round(final_net, 2),
                "stock": round(stock, 1),
                "sessions_left": round(sessions, 1) if sessions != float("inf") else "inf",
                "weight": round(need_weights.get(r, 0), 2),
                "market_price": round(market_prices.get(r, 0), 1),
            }

        try:
            gm = mongo.db.global_modifiers.find_one({"name": "global_modifiers"}, {"session_counter": 1})
            session_number = gm.get("session_counter", 0) if gm else 0
        except Exception:
            session_number = 0

        # Keep fuller fidelity (modifier breakdown, unlocked jobs) for the top few
        # districts only, to keep document size reasonable
        district_scores_detail = []
        for e in (district_scores or [])[:8]:
            entry = {
                "score": round(e[0], 2),
                "key": e[1],
                "display_name": e[2],
                "cost": e[3],
                "rationale": e[4],
                "source": e[5] if len(e) > 5 else "",
            }
            if len(district_scores_detail) < 5:
                entry["mod_breakdown"] = e[6] if len(e) > 6 else []
                entry["unlocked_jobs"] = e[7] if len(e) > 7 else []
            district_scores_detail.append(entry)

        diagnostic = {
            "session_number": session_number,
            "nation": old_nation.get("name", "Unknown"),
            "temperament": old_nation.get("temperament", "?"),
            "pops": state.get("total_pops", 0),
            "idle_pops": state.get("idle_pops", 0),
            "money": state.get("money", 0),
            "money_income": state.get("money_income", 0),
            "open_district_slots": state.get("open_district_slots", 0),
            "personality": {k: round(v, 2) for k, v in personality.items()},
            "initial_goal": initial_goal,
            "strategic_goal": goal,
            "secondary_goal": secondary_goal,
            "future_utility": state.get("future_utility", {}),
            "production_clamp_absorbed": state.get("production_clamp_absorbed", {}),
            "stability_chance_headroom": state.get("stability_chance_headroom", {}),
            "goal_candidates": goal_candidates,
            "upkeep_ratio": round(upkeep_ratio, 2),
            "upkeep_assignments": upkeep_assignments,
            "goal_assignments": goal_assignments,
            "upkeep_log": upkeep_log,
            "goal_job_log": goal_job_log,
            "need_weights": {k: round(v, 2) for k, v in need_weights.items()},
            "job_scores": {k: round(v, 2) for k, v in job_scores.items()},
            "resource_detail": resource_detail,
            "district_scores": district_scores_detail,
            "district_plan": district_plan,
            "district_log": district_log,
            "tech_target": tech_target,
            "desires": desires,
            "decision_log": log,
        }

        # --- Persist AI state ---
        new_nation["ai_state"] = {
            "strategic_goal":     goal,
            "secondary_goal":     secondary_goal,
            "goal_candidates":    goal_candidates,
            "upkeep_assignments": upkeep_assignments,
            "upkeep_ratio":       round(upkeep_ratio, 2),
            "goal_assignments":   goal_assignments,
            "tech_target":        tech_target,
            "planned_district":   district_plan,
            "decision_log":       log,
            "diagnostic":         diagnostic,
        }

    except Exception as e:
        import traceback
        new_nation["ai_state"] = {"decision_log": [f"AI error: {e}", traceback.format_exc()]}
    finally:
        # Free tile cache immediately — it accumulates across all 180 AI nations in the
        # tick loop and is never needed again after this function returns.
        old_nation.pop("_legal_placement_cache", None)

    name = old_nation.get("name", "Unknown")
    return f"{name}: AI decision ({len(log)} actions)\n"


# ---------------------------------------------------------------------------
# Mid-session AI market matching (NATION_CROSS_TICK_FUNCTION)
# ---------------------------------------------------------------------------


def _post_trade_build_and_rejob(old_nation, new_nation, log_lines):
    """After trade matching: if the nation's planned district is now affordable
    with newly received resources, build it and assign newly idle pops.

    Returns True if a district was built this call.
    """
    ai_st = new_nation.get("ai_state") or {}
    plan = ai_st.get("planned_district")
    if not plan or not plan.get("key"):
        return False

    cost = plan.get("cost", {})
    storage = new_nation.get("resource_storage", {})
    money = new_nation.get("money", old_nation.get("money", 0))

    if not (money >= cost.get("money", 0) and
            all(storage.get(r, 0) >= amt for r, amt in cost.items() if r != "money")):
        return False

    def_key = plan["key"]
    nation_name = old_nation.get("name", "")

    import uuid
    district_id = uuid.uuid4().hex[:8]
    coord, node_key = None, ""
    dd = None
    # Nomads build the district on the nation doc only — never claim a map tile.
    if plan.get("source", "db") == "db" and not _nation_is_nomadic(old_nation):
        dd = mongo.db.district_defs.find_one({"key": def_key})
        if dd:
            from calculations.field_calculations import _compute_legal_placement
            old_nation.pop("_legal_placement_cache", None)
            legal = _compute_legal_placement(old_nation)
            stored_weights = ai_st.get("diagnostic", {}).get("need_weights", {})
            coord, _ = _pick_district_tile(legal, dd, def_key, stored_weights, {})
            if coord:
                node_key = _claim_district_tile(
                    nation_name, district_id, def_key,
                    dd.get("display_name", def_key), coord
                )

    new_entry = {"_id": district_id, "def_key": def_key, "node": node_key, "upgrades": []}
    districts = list(new_nation.get("districts", old_nation.get("districts", [])))
    _insert_filling_empty_slot(districts, new_entry, lambda d: not d.get("def_key") and not d.get("type"))
    new_nation["districts"] = districts

    for r, amt in cost.items():
        if r == "money":
            new_nation["money"] = new_nation.get("money", old_nation.get("money", 0)) - amt
        else:
            storage[r] = storage.get(r, 0) - amt
    new_nation["resource_storage"] = storage

    ai_st["planned_district"] = None

    coord_str = f" at ({coord[0]},{coord[1]})" if coord else ""
    node_str = f" — node: {node_key}" if node_key else ""
    log_lines.append(f"{nation_name}: post-trade built {plan.get('display_name', def_key)}{coord_str}{node_str}")

    # Re-evaluate idle pops: assign any pops freed up after the new district
    # may have unlocked better jobs.
    try:
        total_pops = new_nation.get("pops", old_nation.get("pops", 0))
        assigned_pops = sum(v for v in new_nation.get("jobs", {}).values() if isinstance(v, (int, float)))
        idle_pops = max(0, total_pops - assigned_pops)
        if idle_pops <= 0:
            return True

        # Build a merged state using old computed fields + new resources/districts
        merged = dict(old_nation)
        merged["districts"] = new_nation.get("districts", [])
        merged["resource_storage"] = storage
        merged["money"] = new_nation.get("money", 0)

        state = evaluate_nation_state(merged)

        goal_data = ai_st.get("strategic_goal") or {}
        if not goal_data:
            return True

        prices = get_stored_market_prices(old_nation)

        # projected_net: base net_production + contributions from already-assigned
        # jobs, with the first units of production filling any clamp-absorbed
        # malus (e.g. Savior's -1) before counting.
        projected_net = dict(state["net_production"])
        clamp_absorbed = dict(state.get("production_clamp_absorbed") or {})
        all_jobs_data = json_data.get("jobs", {})
        for jk, cnt in new_nation.get("jobs", {}).items():
            jd = state["available_jobs"].get(jk) or all_jobs_data.get(jk, {})
            for r, amt in jd.get("production", {}).items():
                if isinstance(amt, (int, float)):
                    projected_net[r] = projected_net.get(r, 0) + _consume_clamp_absorbed(clamp_absorbed, r, amt * cnt)
            for r, amt in jd.get("upkeep", {}).items():
                if isinstance(amt, (int, float)):
                    projected_net[r] = projected_net.get(r, 0) - amt * cnt
        state["production_clamp_absorbed_remaining"] = clamp_absorbed

        new_assignments, job_log, _ = assign_goal_jobs(
            state, goal_data, idle_pops, projected_net, None, prices
        )
        if new_assignments:
            existing_jobs = dict(new_nation.get("jobs", {}))
            for jk, cnt in new_assignments.items():
                existing_jobs[jk] = existing_jobs.get(jk, 0) + cnt
            new_nation["jobs"] = existing_jobs
            for entry in job_log:
                log_lines.append(f"{nation_name}: post-trade {entry}")
    except Exception as e:
        log_lines.append(f"{nation_name}: post-trade job re-eval error: {e}")

    return True

AI_MATCH_MAX_DELAY = 1  # "single turn of trade distance": delay ≤ 1


def _build_ai_distance_cache(buyer_names, all_ai_names, trade_speeds, tiles_raw, portal_map, move_costs, trade_wonder_ids=None):
    """Run one Dijkstra per buyer nation (reusing pre-loaded tiles) and return
    {buyer_name: {seller_name: delay}} for all reachable seller names.
    Unreachable pairs are omitted (treat as infinite delay → skip match).
    """
    from helpers.trade_route_helpers import _dijkstra_from_cities, compute_delay
    if trade_wonder_ids is None:
        trade_wonder_ids = set()
    cache = {}
    for buyer_name in buyer_names:
        seller_names = [n for n in all_ai_names if n != buyer_name]
        if not seller_names:
            cache[buyer_name] = {}
            continue
        reached = _dijkstra_from_cities(buyer_name, seller_names, tiles_raw, portal_map, move_costs, trade_wonder_ids=trade_wonder_ids)
        src_speed = trade_speeds.get(buyer_name, 7)
        delays = {}
        for tgt_name, cost in reached.items():
            tgt_speed = trade_speeds.get(tgt_name, 7)
            delays[tgt_name] = compute_delay(cost, src_speed, tgt_speed)
        cache[buyer_name] = delays
    return cache


def _run_ai_market_matching(old_nations, new_nations, log_lines):
    """
    Core matching logic for AI market matching.

    Mutates new_nations in place.  Returns a set of nation indices (into
    new_nations) that received buy-order resources so the caller can run
    post-trade district purchase for them.

    Both old_nations and new_nations must be parallel lists of the same
    length and in the same order.
    """
    from helpers.trade_route_helpers import (
        _load_trade_tiles, _build_portal_map,
        _terrain_move_costs, _get_trade_source_wonder_ids,
    )

    buyers_who_received = set()
    old_nation_by_idx   = {i: n for i, n in enumerate(old_nations)}

    # --- Step 1: Collect all AI buy and sell orders ---
    buy_orders  = []
    sell_orders = []
    for idx, nation in enumerate(new_nations):
        if nation.get("temperament", "Player") == "Player":
            continue
        for didx, desire in enumerate(nation.get("resource_desires", [])):
            qty = desire.get("quantity", 0)
            if qty <= 0:
                continue
            entry = {
                "resource":    desire["resource"],
                "trade_type":  desire["trade_type"],
                "price":       desire.get("price", 0),
                "quantity":    qty,
                "nation_idx":  idx,
                "desire_idx":  didx,
                "nation_name": nation.get("name", ""),
            }
            if "Buy" in desire["trade_type"]:
                buy_orders.append(entry)
            elif "Sell" in desire["trade_type"]:
                sell_orders.append(entry)

    if not buy_orders or not sell_orders:
        return buyers_who_received

    # --- Step 2: Precompute trade distances (one Dijkstra per buyer nation) ---
    buyer_names      = list({b["nation_name"] for b in buy_orders})
    seller_names_set = {s["nation_name"] for s in sell_orders}
    all_ai_names     = list(set(buyer_names) | seller_names_set)

    name_to_old = {n.get("name", ""): n for n in old_nations}
    trade_speeds = {
        name: (name_to_old.get(name) or {}).get("trade_speed") or 7
        for name in all_ai_names
    }

    tiles_raw        = _load_trade_tiles(all_ai_names)
    portal_map       = _build_portal_map()
    move_costs       = _terrain_move_costs()
    trade_wonder_ids = _get_trade_source_wonder_ids()

    distance_cache = _build_ai_distance_cache(
        buyer_names, all_ai_names, trade_speeds, tiles_raw, portal_map, move_costs,
        trade_wonder_ids=trade_wonder_ids,
    )

    # --- Step 3: Sort and match ---
    buy_orders.sort(key=lambda x: (
        0 if x["trade_type"] == "Need to Buy" else 1, -x["price"]
    ))
    sell_orders.sort(key=lambda x: (
        0 if x["trade_type"] == "Need to Sell" else 1, x["price"]
    ))

    for buy in buy_orders:
        buyer_name   = buy["nation_name"]
        buyer_delays = distance_cache.get(buyer_name, {})
        for sell in sell_orders:
            if buy["resource"] != sell["resource"]:
                continue
            if buy["nation_idx"] == sell["nation_idx"]:
                continue
            if buy["price"] < sell["price"]:
                continue
            if sell["quantity"] <= 0 or buy["quantity"] <= 0:
                continue

            seller_name = sell["nation_name"]
            delay = buyer_delays.get(seller_name)
            if delay is None or delay > AI_MATCH_MAX_DELAY:
                continue

            price    = sell["price"]
            resource = buy["resource"]

            buyer  = new_nations[buy["nation_idx"]]
            seller = new_nations[sell["nation_idx"]]

            b_storage = buyer.setdefault("resource_storage", {})
            s_storage = seller.setdefault("resource_storage", {})

            # Limit by seller stock, buyer funds, and buyer storage capacity
            qty = min(buy["quantity"], sell["quantity"], s_storage.get(resource, 0))
            if price > 0:
                qty = min(qty, int(buyer.get("money", 0) // price))
            b_cap = (buyer.get("nation_resource_capacity") or {}).get(resource)
            if b_cap is not None:
                qty = min(qty, b_cap - b_storage.get(resource, 0))
            qty = int(qty)
            if qty <= 0:
                continue
            total = qty * price

            b_storage[resource] = b_storage.get(resource, 0) + qty
            s_storage[resource] = s_storage.get(resource, 0) - qty
            buyer["money"] = buyer.get("money", 0) - total
            new_seller_money = seller.get("money", 0) + total
            s_money_cap = seller.get("money_capacity")
            if s_money_cap is not None:
                new_seller_money = min(new_seller_money, s_money_cap)
            seller["money"] = new_seller_money

            buy["quantity"]  -= qty
            sell["quantity"] -= qty
            buyer["resource_desires"][buy["desire_idx"]]["quantity"]   = buy["quantity"]
            seller["resource_desires"][sell["desire_idx"]]["quantity"] = sell["quantity"]

            buyers_who_received.add(buy["nation_idx"])
            log_lines.append(
                f"{buyer_name} bought {qty}× {resource} from {seller_name} "
                f"@ {price} (delay {delay})"
            )

    return buyers_who_received


def run_ai_market_matching_standalone(app_ctx=None):
    """
    Self-contained AI market matching: loads nation data fresh from the DB,
    runs all matching, calls post-trade district purchase, then commits every
    changed nation directly via system_request_change / system_approve_change.

    Designed to be called from a background thread so it never blocks an HTTP
    request.  Pass the Flask app object (or an app-context push) when calling
    from outside a request context so DB access works correctly.

    Returns a human-readable summary string.
    """
    from helpers.change_helpers import system_request_change, system_approve_change
    from helpers.data_helpers import get_data_on_category
    from calculations.field_calculations import calculate_all_fields
    from copy import deepcopy
    from pymongo import ASCENDING

    ctx = None
    if app_ctx is not None:
        ctx = app_ctx.app_context()
        ctx.push()

    try:
        nation_schema, nation_db = get_data_on_category("nations")

        old_nations = list(nation_db.find().sort("name", ASCENDING))
        new_nations = []
        for n in old_nations:
            if n:
                n.update(calculate_all_fields(n, nation_schema, "nation"))
                new_nations.append(deepcopy(n))

        log_lines = []

        try:
            buyers_who_received = _run_ai_market_matching(old_nations, new_nations, log_lines)
        except Exception as e:
            log_lines.append(f"AI market matching error: {e}")
            buyers_who_received = set()

        # Post-trade district purchase for each buyer that received resources.
        for bidx in buyers_who_received:
            try:
                old_n = old_nations[bidx] if bidx < len(old_nations) else None
                new_n = new_nations[bidx] if bidx < len(new_nations) else None
                if old_n is None or new_n is None:
                    continue
                _post_trade_build_and_rejob(old_n, new_n, log_lines)
            except Exception as e:
                name = (new_nations[bidx].get("name", "?")
                        if bidx < len(new_nations) else "?")
                log_lines.append(f"{name}: post-trade district error: {e}")

        # Determine which nations actually changed and save them directly to DB.
        # Only save nations that were buyers, sellers, or had a post-trade build
        # so we don't touch untouched nation documents.
        changed_indices = set(buyers_who_received)
        # Also include sellers (their storage and money changed).
        for idx, (old_n, new_n) in enumerate(zip(old_nations, new_nations)):
            if idx in changed_indices:
                continue
            # Quick check: money or resource_storage changed.
            if (new_n.get("money") != old_n.get("money") or
                    new_n.get("resource_storage") != old_n.get("resource_storage") or
                    new_n.get("resource_desires") != old_n.get("resource_desires")):
                changed_indices.add(idx)

        saved = 0
        for idx in changed_indices:
            try:
                old_n = old_nations[idx]
                new_n = new_nations[idx]
                change_id = system_request_change(
                    data_type="nations",
                    item_id=old_n["_id"],
                    change_type="Update",
                    before_data=deepcopy(old_n),
                    after_data=deepcopy(new_n),
                    reason="AI Market Matching Tick",
                )
                if change_id:
                    system_approve_change(change_id)
                    saved += 1
            except Exception as e:
                name = old_nations[idx].get("name", "?")
                log_lines.append(f"{name}: save error: {e}")

        log_lines.append(f"Saved {saved} nation(s).")
        summary = "\n".join(log_lines) + "\n" if log_lines else ""
        print(summary)
        return summary

    finally:
        if ctx is not None:
            ctx.pop()


def ai_market_matching_tick(old_nations, new_nations, schema):
    """
    Tick-system entry point for AI market matching.  Operates on the
    already-loaded old_nations / new_nations lists provided by the tick
    infrastructure (changes are persisted by the tick's batch save loop).

    For standalone / async use outside the tick system call
    run_ai_market_matching_standalone() instead — it loads its own data and
    commits changes directly to the DB so it completes reliably without
    depending on the tick's save pass.
    """
    log_lines = []
    try:
        buyers_who_received = _run_ai_market_matching(old_nations, new_nations, log_lines)
    except Exception as e:
        log_lines.append(f"AI market matching error: {e}")
        buyers_who_received = set()

    old_nation_by_idx = {i: n for i, n in enumerate(old_nations)}
    for bidx in buyers_who_received:
        try:
            old_n = old_nation_by_idx.get(bidx)
            new_n = new_nations[bidx] if bidx < len(new_nations) else None
            if old_n is None or new_n is None:
                continue
            _post_trade_build_and_rejob(old_n, new_n, log_lines)
        except Exception as e:
            name = (new_nations[bidx].get("name", "?")
                    if bidx < len(new_nations) else "?")
            log_lines.append(f"{name}: post-trade district error: {e}")

    return "\n".join(log_lines) + "\n" if log_lines else ""
