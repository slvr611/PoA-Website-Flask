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

GOAL_DISTRICT_AFFINITY = {
    "stabilize_economy": {
        "job_resources": [],
        "categories": [],
        "bonus": 1.5,
        "dynamic": True,
    },
    "stabilize_nation": {
        "job_resources": ["stability_gain_chance", "stability_loss_chance"],
        "categories": [],
        "bonus": 1.5,
    },
    "grow_economy": {
        "job_resources": ["food", "wood", "stone", "iron", "mounts", "money_income"],
        "categories": ["production", "economic"],
        "bonus": 1.3,
    },
    "prepare_war": {
        "job_resources": ["iron", "gunpowder"],
        "categories": ["military"],
        "bonus": 1.4,
    },
    "develop_technology": {
        "job_resources": ["research"],
        "categories": [],
        "bonus": 1.5,
    },
    "grow_population": {
        "modifier_fields": ["effective_pop_capacity"],
        "categories": ["housing"],
        "bonus": 1.4,
    },
    "convert_population": {
        "job_resources": ["backlash_reduction"],
        "categories": ["religious"],
        "bonus": 1.4,
    },
    "expand_trade": {
        "modifier_fields": ["trade_slots", "money_income"],
        "categories": ["trade"],
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
    persistent_job_keys = {"undead", "partial_vampire", "revolutionary"}
    base_excess = dict(old_nation.get("resource_excess", {}))
    jobs_map = json_data.get("jobs", {})
    for jk, count in old_nation.get("jobs", {}).items():
        if count <= 0 or jk in persistent_job_keys:
            continue
        jdata = jobs_map.get(jk, {})
        for r, amt in jdata.get("production", {}).items():
            if isinstance(amt, (int, float)):
                base_excess[r] = base_excess.get(r, 0) - amt * count
        for r, amt in jdata.get("upkeep", {}).items():
            if isinstance(amt, (int, float)):
                base_excess[r] = base_excess.get(r, 0) + amt * count
    resource_excess  = base_excess
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

    # Available jobs — reuse existing requirement checker
    from calculations.field_calculations import check_job_requirements
    jobs_data = json_data.get("jobs", {})
    available_jobs = {}
    for jk, jdata in jobs_data.items():
        if old_nation.get(f"locks_{jk}", 0):
            continue
        if check_job_requirements(old_nation, jdata, {}):
            available_jobs[jk] = jdata

    # Persistent jobs that survive cleanup and shouldn't be re-assigned
    persistent = {"undead", "partial_vampire", "revolutionary"}
    persistent_assigned = sum(
        old_nation.get("jobs", {}).get(j, 0) for j in persistent
    )
    total_pops = old_nation.get("pop_count", 0)
    idle_pops  = max(0, total_pops - persistent_assigned)

    # District inventory
    existing_def_keys = set()
    existing_types    = set()
    for d in old_nation.get("districts", []):
        if d.get("def_key"):
            existing_def_keys.add(d["def_key"])
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
        "existing_types":        existing_types,
        "open_district_slots":   open_district_slots,
        "district_cost_mod":     district_cost_mod,
        "trade_speed":           old_nation.get("trade_speed", 7),
        "territory_types":       territory_types,
        "resource_capacity":     old_nation.get("nation_resource_capacity", {}),
    }


# ---------------------------------------------------------------------------
# Need weights
# ---------------------------------------------------------------------------

def _weights_from_net(net_production, stockpiles, prices, money_income, resource_capacity=None):
    """
    Core weight computation from a net-production snapshot.
    Called by compute_need_weights and by assign_ai_jobs after each pop assignment.

    `prices` should be dynamic market prices (from get_stored_market_prices) or
    static base prices as a fallback.  Surplus floor is 0.3 + price/100.

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
        price = prices.get(r, 10)

        if net >= 0:
            if stockpile < 5 and net < 1:
                w = 1.3
            else:
                w = 0.3 + price / 100.0
        else:
            if stockpile > 0:
                sessions = stockpile / (-net)
            else:
                sessions = 0.0

            if sessions < 2:
                w = 5.0
            elif sessions < 4:
                w = 3.0
            else:
                surplus_w = 0.3 + price / 100.0
                w = surplus_w + (2.0 - surplus_w) * (4.0 / sessions)

        # Penalize resources at or near stockpile cap — no point producing more
        if resource_capacity and net > 0:
            cap = resource_capacity.get(r, 0)
            if cap > 0 and stockpile >= cap:
                w = 0.05
            elif cap > 0 and stockpile >= cap * 0.8:
                w *= 0.3

        weights[r] = w

    weights["money"] = 1.2 if money_income >= 0 else 2.5
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
    )


# ---------------------------------------------------------------------------
# Job scoring
# ---------------------------------------------------------------------------

def score_jobs(state, need_weights, prices=None):
    """
    Compute efficiency score for each available job.
    Factors in: resource need weights, market price normalisation, district bonus.
    """
    prices = prices if prices is not None else _base_prices()
    nation_districts = state["existing_def_keys"] | state["existing_types"]
    scores = {}

    for jk, job in state["available_jobs"].items():
        prod_value  = 0.0
        upkeep_cost = 0.0

        for field, amount in job.get("production", {}).items():
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
            projected_net, state["stockpiles"], prices, state["money_income"]
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

    Returns (upkeep_assignments, remaining_pops, projected_net, upkeep_log, upkeep_ratio).
    upkeep_ratio = fraction of idle pops consumed by upkeep (0.0 – 1.0).
    """
    if state["idle_pops"] <= 0:
        return {}, 0, dict(state["net_production"]), [], 0.0

    prices = prices if prices is not None else _base_prices()
    projected_net = dict(state["net_production"])
    assignments = {}
    pops_remaining = state["idle_pops"]
    total_idle = state["idle_pops"]
    log = []

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
            break

        # Only consider jobs that produce a resource currently in deficit
        deficit_resources = {r for r, net in projected_net.items() if net < 0}
        curr_weights = _weights_from_net(
            projected_net, state["stockpiles"], prices, state["money_income"]
        )
        job_scores = score_jobs(state, curr_weights, prices)
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
            break

        best = max(positive, key=lambda k: positive[k])
        assignments[best] = assignments.get(best, 0) + 1
        pops_remaining -= 1

        job = state["available_jobs"][best]
        for r, amt in job.get("production", {}).items():
            if isinstance(amt, (int, float)) and r in projected_net:
                projected_net[r] = projected_net.get(r, 0) + amt
        for r, amt in job.get("upkeep", {}).items():
            if isinstance(amt, (int, float)) and r in projected_net:
                projected_net[r] = projected_net.get(r, 0) - amt

    upkeep_pops = total_idle - pops_remaining
    upkeep_ratio = upkeep_pops / total_idle if total_idle > 0 else 0.0

    for jk, cnt in sorted(assignments.items(), key=lambda x: -x[1]):
        if cnt > 0:
            job = state["available_jobs"].get(jk, {})
            log.append(f"Upkeep: {cnt}× {job.get('display_name', jk)}")
    if upkeep_pops > 0:
        log.append(f"Upkeep floor: {upkeep_pops}/{total_idle} pops ({upkeep_ratio:.0%} of workforce)")

    return (
        {k: v for k, v in assignments.items() if v > 0},
        pops_remaining,
        projected_net,
        log,
        upkeep_ratio,
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
            if deficit_count == 0:
                candidates.append({
                    "type": goal_type, "score": 0,
                    "display_name": goal_def["display_name"],
                    "rationale": "No deficits",
                })
                continue
            situation += critical_count * 20 + non_critical_deficit * 15
            rationale_parts.append(f"{critical_count} critical, {non_critical_deficit} other deficits")
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
            situation += open_slots * 8
            rationale_parts.append(f"{open_slots} open district slots (+{open_slots * 8})")
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
            stockpile_key, base_w = PRODUCTION_FIELD_MAP[field]
            weight = base_w
            scale = _price_scale(stockpile_key, prices) if stockpile_key else 1.0
            contrib = base_w * value * scale
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

    Returns (total_value, [job_detail_dict, ...]).
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

        # Find existing jobs that produce the same primary resource
        same_role = existing_by_resource.get(primary_resource, [])

        if same_role:
            # Marginal improvement over the best existing producer of this resource
            best_existing_name, best_existing_score, best_existing_pops = max(same_role, key=lambda x: x[1])
            marginal = raw_score - best_existing_score
            est_pops = sum(p for _, _, p in same_role)
            comparison_score = best_existing_score
            comparison_name = available_jobs[best_existing_name].get("display_name", best_existing_name)
            comparison_type = "replaces"
        else:
            # New role — estimate pops based on deficit
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

    return total, unlocked


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

    # Pre-compute discretionary pop info once for all district evaluations.
    # This tells _district_modifier_value how many pops are likely to work
    # each job beyond the upkeep floor, based on relative job scores.
    upkeep_total = sum(nation_jobs.values()) if nation_jobs else 0
    remaining_pops = max(0, state["idle_pops"] - upkeep_total)
    job_shares = {}
    if remaining_pops > 0:
        all_job_scores = score_jobs(state, need_weights, market_buy_prices)
        total_positive = sum(max(0, s) for s in all_job_scores.values())
        if total_positive > 0:
            job_shares = {
                jk: max(0, sc) / total_positive
                for jk, sc in all_job_scores.items()
                if sc > 0
            }
    discretionary_info = {"remaining_pops": remaining_pops, "job_shares": job_shares}

    # --- DB-driven district defs ---
    try:
        defs = list(mongo.db.district_defs.find({}, {"_id": 0}))
    except Exception:
        defs = []

    for dd in defs:
        dk = dd.get("key", "")
        if not dk or dk in state["existing_def_keys"]:
            continue

        if not check_district_requirements(old_nation, dd):
            continue

        raw_cost    = dd.get("cost", {})
        actual_cost = _apply_district_cost_mod(raw_cost, state["district_cost_mod"])

        can_afford_now = all(
            state["stockpiles"].get(r, 0) >= amt
            for r, amt in actual_cost.items()
            if r != "money"
        ) and state["money"] >= actual_cost.get("money", 0)

        mod_value, mod_breakdown = _district_modifier_value(dd.get("modifiers", []), need_weights, market_buy_prices, state.get("territory_types"), nation_jobs, state, discretionary_info)

        job_value, unlocked_jobs = _unlocked_jobs_value(dk, state, need_weights, market_buy_prices, nation_jobs)

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
        ) * 0.15 + (actual_cost.get("money", 0) / max(state["money"] + 1, 1)) * 5

        score = mod_value + job_value + market_bonus - cost_penalty
        if score <= 0:
            continue

        parts = [f"modifiers {mod_value:.1f}"]
        if unlocked_jobs:
            job_names = ", ".join(f"{j['name']} ({j['scaled_score']})" for j in unlocked_jobs)
            parts.append(f"unlocks jobs +{job_value:.1f} [{job_names}]")
        if market_bonus > 0.5:
            parts.append(f"market +{market_bonus:.1f}")
        parts.append(f"cost penalty -{cost_penalty:.1f}")
        parts.append("[can afford]" if can_afford_now else "[saving]")
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


def update_district_plan(old_nation, new_nation, state, goals, personality, market_buy_prices, need_weights, log, job_assignments=None):
    """
    If the nation has an existing plan, check if it's still best and affordable.
    Otherwise pick the top-scored district as the new plan.
    Builds immediately if affordable.
    Returns updated plan dict (or None) and appends to log.
    """
    current_plan = old_nation.get("ai_state", {}).get("planned_district")
    scored       = score_buildable_districts(old_nation, state, need_weights, market_buy_prices, job_assignments)

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
        districts.append(new_entry)
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


def _select_best_city(old_nation, state):
    """
    For grow_population: pick the best city to build (or replace).
    Evaluates conditional modifiers against the nation's current state to find
    the city type that would give the most effective_pop_capacity.
    Returns a plan dict with source='city', or None if no city avenue exists.
    """
    cities_data = json_data.get("cities", {})
    city_slots = old_nation.get("city_slots", 0)
    existing_cities = old_nation.get("cities", [])
    open_city_slots = max(0, city_slots - len(existing_cities))

    if city_slots <= 0:
        return None

    # Score each city type by total pop cap including conditional bonuses.
    # At equal pop cap, prefer cheaper cities (lower total resource cost).
    city_scores = []
    for ct, cdata in cities_data.items():
        total_cap = _estimate_city_pop_cap(ct, cities_data, old_nation)
        cost = dict(cdata.get("cost", {}))
        total_cost = sum(cost.values())
        city_scores.append((ct, cdata.get("display_name", ct), total_cap, cost, total_cost))

    if not city_scores:
        return None

    city_scores.sort(key=lambda x: (x[2], -x[4]), reverse=True)

    if open_city_slots > 0:
        best = city_scores[0]
        return {
            "key": best[0],
            "display_name": f"City: {best[1]}",
            "cost": best[3],
            "rationale": f"Build new city (+{best[2]} pop cap), {open_city_slots} slot(s) open",
            "sessions_saving": 0,
            "source": "city",
        }

    # No open slots — check if we can replace the weakest existing city
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


def evaluate_goal_district(old_nation, new_nation, state, goal, need_weights, prices, upkeep_assignments, log):
    """
    Score buildable districts with a goal-alignment bonus on top of the base score.
    For grow_population, prioritises city construction over districts.
    Returns (district_plan, district_scores, district_log).
    """
    # For grow_population, check cities first — they're the primary source of pop cap
    if goal.get("type") == "grow_population":
        city_plan = _select_best_city(old_nation, state)
        if city_plan:
            district_log = []
            current_plan = old_nation.get("ai_state", {}).get("planned_district")
            if current_plan and current_plan.get("source") == "city":
                city_plan["sessions_saving"] = current_plan.get("sessions_saving", 0) + 1

            # Check if affordable now (cities aren't auto-built by the tick, just save resources)
            cost = city_plan.get("cost", {})
            money_ok = state["money"] >= cost.get("money", 0)
            res_ok = all(
                state["stockpiles"].get(r, 0) >= amt
                for r, amt in cost.items() if r != "money"
            )
            if money_ok and res_ok:
                district_log.append(f"City plan ready to build: {city_plan['display_name']} — {city_plan['rationale']}")
            else:
                district_log.append(f"Saving for city: {city_plan['display_name']} (session {city_plan.get('sessions_saving', 0) + 1}) — {city_plan['rationale']}")

            scored = score_buildable_districts(old_nation, state, need_weights, prices, upkeep_assignments)
            log.extend(district_log)
            return city_plan, scored, district_log

    scored = score_buildable_districts(old_nation, state, need_weights, prices, upkeep_assignments)
    if not scored:
        return None, [], []

    goal_type = goal.get("type", "")
    affinity = GOAL_DISTRICT_AFFINITY.get(goal_type, {})
    bonus_mult = affinity.get("bonus", 1.0)
    goal_categories = set(affinity.get("categories", []))
    goal_job_resources = set(affinity.get("job_resources", []))
    goal_modifier_fields = set(affinity.get("modifier_fields", []))
    is_dynamic = affinity.get("dynamic", False)

    # For stabilize_economy, the "goal resources" are whatever is in deficit
    if is_dynamic:
        goal_job_resources = {
            r for r, net in state["net_production"].items() if net < 0
        }

    adjusted = []
    for entry in scored:
        base_score = entry[0]
        dk = entry[1]
        display_name = entry[2]
        cost = entry[3]
        rationale = entry[4]
        source = entry[5]
        mod_breakdown = entry[6] if len(entry) > 6 else []
        unlocked_jobs = entry[7] if len(entry) > 7 else []

        goal_match = False
        match_reason = ""

        # Check district category from DB
        try:
            dd = mongo.db.district_defs.find_one({"key": dk}, {"category": 1, "_id": 0})
            if dd and dd.get("category", "") in goal_categories:
                goal_match = True
                match_reason = f"category '{dd['category']}'"
        except Exception:
            pass

        # Check if unlocked jobs produce goal-relevant resources
        if not goal_match and unlocked_jobs:
            for uj in unlocked_jobs:
                for p in uj.get("production", []):
                    field = p.get("field", "")
                    if field in goal_job_resources:
                        goal_match = True
                        match_reason = f"unlocks {uj['name']} producing {field}"
                        break
                if goal_match:
                    break

        # Check modifier fields
        if not goal_match and goal_modifier_fields and mod_breakdown:
            for field, val, weight, scale, contrib, matched in mod_breakdown:
                if field in goal_modifier_fields and val > 0:
                    goal_match = True
                    match_reason = f"modifier {field}"
                    break

        # Check if modifiers produce goal-relevant resources
        if not goal_match and goal_job_resources and mod_breakdown:
            for field, val, weight, scale, contrib, matched in mod_breakdown:
                if matched and val > 0:
                    res_key = field
                    if field.endswith("_production"):
                        res_key = field[:-len("_production")]
                    if res_key in goal_job_resources:
                        goal_match = True
                        match_reason = f"produces {res_key}"
                        break

        final_score = base_score * bonus_mult if goal_match else base_score
        goal_tag = f" [GOAL: {match_reason} ×{bonus_mult}]" if goal_match else ""

        adjusted.append((
            final_score, dk, display_name, cost,
            rationale + goal_tag, source,
            mod_breakdown, unlocked_jobs,
        ))

    adjusted.sort(key=lambda x: x[0], reverse=True)

    district_log = []
    dummy = deepcopy(new_nation)
    district_plan = update_district_plan(
        old_nation, dummy, state, [], {},
        prices, need_weights, district_log, upkeep_assignments,
    )

    # If existing plan is not in top-3 of goal-adjusted scores, re-evaluate
    top_keys = {s[1] for s in adjusted[:3]}
    if district_plan and district_plan.get("key") not in top_keys:
        best = adjusted[0]
        district_plan = {
            "key": best[1],
            "display_name": best[2],
            "cost": best[3],
            "rationale": best[4],
            "sessions_saving": 0,
            "source": best[5],
        }
        district_log.append(f"New district goal (goal-aligned): {best[2]} — {best[4]}")
    elif not district_plan and adjusted:
        best = adjusted[0]
        district_plan = {
            "key": best[1],
            "display_name": best[2],
            "cost": best[3],
            "rationale": best[4],
            "sessions_saving": 0,
            "source": best[5],
        }
        district_log.append(f"New district goal: {best[2]} — {best[4]}")

    # Attempt to build if affordable
    if district_plan:
        key = district_plan["key"]
        cost = district_plan.get("cost", {})
        money_ok = state["money"] >= cost.get("money", 0)
        res_ok = all(
            state["stockpiles"].get(r, 0) >= amt
            for r, amt in cost.items() if r != "money"
        )
        if money_ok and res_ok and state["open_district_slots"] > 0:
            new_entry = (
                {"def_key": key, "node": "", "upgrades": []}
                if district_plan.get("source") == "db"
                else {"type": key, "node": "", "era": 1}
            )
            districts = list(new_nation.get("districts", deepcopy(old_nation.get("districts", []))))
            districts.append(new_entry)
            new_nation["districts"] = districts

            storage = dict(new_nation.get("resource_storage", deepcopy(old_nation.get("resource_storage", {}))))
            for r, amt in cost.items():
                if r == "money":
                    new_nation["money"] = new_nation.get("money", old_nation.get("money", 0)) - amt
                else:
                    storage[r] = storage.get(r, 0) - amt
            new_nation["resource_storage"] = storage

            district_log.append(f"Built district: {district_plan['display_name']} (saved {district_plan.get('sessions_saving', 0)} sessions)")
            district_plan = None
        else:
            sessions = district_plan.get("sessions_saving", 0)
            district_plan["sessions_saving"] = sessions + 1
            district_log.append(f"Saving for: {district_plan['display_name']} (session {sessions + 1})")

    log.extend(district_log)
    return district_plan, adjusted, district_log


# ---------------------------------------------------------------------------
# Goal-driven job assignment
# ---------------------------------------------------------------------------

def _compute_goal_resource_needs(goal, district_plan, state):
    """
    Return a dict of {resource: boost_weight} for resources the goal cares about.
    The boost is additive on top of normal need weights.
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
            if current < amt:
                # Any shortfall gets a strong minimum boost — even 1 unit short matters
                deficit_ratio = min(3.0, (amt - current) / max(amt, 1))
                boosts[r] = boosts.get(r, 0) + max(3.0, 2.0 * deficit_ratio)
            else:
                # Already have enough of this resource for the plan — suppress it
                boosts[r] = boosts.get(r, 0) - 1.0

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
        # Boost whatever is in deficit
        for r, net in state["net_production"].items():
            if net < 0:
                boosts[r] = boosts.get(r, 0) + 2.0
    elif goal_type == "grow_economy":
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

    goal_boosts = _compute_goal_resource_needs(goal, district_plan, state)

    while pops_left > 0:
        # Compute base weights from current projected production
        # Include resource_capacity so capped resources get deprioritized
        base_weights = _weights_from_net(
            projected_net, state["stockpiles"], prices, state["money_income"],
            state.get("resource_capacity"),
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
        # These fields have small production amounts (0.05–0.15) so the weight
        # must be very large to compete with real resources like food/wood.
        if goal_type == "stabilize_nation":
            goal_weights["stability_gain_chance"] = goal_weights.get("stability_gain_chance", 0.6) + 40.0
            goal_weights["stability_loss_chance"] = goal_weights.get("stability_loss_chance", -0.8) - 30.0

        job_scores = score_jobs(state, goal_weights, prices)
        positive = {k: v for k, v in job_scores.items() if v > 0.05}
        if not positive:
            break

        best = max(positive, key=lambda k: positive[k])

        # Check if this job's upkeep would create a serious new deficit
        job = state["available_jobs"][best]
        upkeep_ok = True
        for r, amt in job.get("upkeep", {}).items():
            if isinstance(amt, (int, float)) and r in projected_net:
                new_net = projected_net.get(r, 0) - amt
                if new_net < 0:
                    stockpile = state["stockpiles"].get(r, 0)
                    if stockpile > 0:
                        sessions = stockpile / (-new_net)
                    else:
                        sessions = 0
                    if sessions < 2:
                        upkeep_ok = False
                        break

        if not upkeep_ok:
            # Try next best job that doesn't create a critical deficit
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
                        if new_net < 0:
                            stk = state["stockpiles"].get(r, 0)
                            s = stk / (-new_net) if stk > 0 else 0
                            if s < 2:
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
                projected_net[r] = projected_net.get(r, 0) + amt
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

def generate_goal_trade_desires(state, goal, personality, district_plan, projected_net, prices=None):
    """
    Build trade desires informed by the strategic goal.
    Each desire gets a source tag: 'survival', 'goal', or 'opportunistic'.
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

        # --- Survival buy: only when buffer is actually running low ---
        if sessions < 5:
            urgency = max(0.0, 1.0 - sessions / 5.0)
            mult = 1.05 + urgency * 0.15
            price = int(round(mkt * mult / 5)) * 5
            qty = 1 if is_luxury else min(5, max(1, int(abs(net) * 2 + 1)))
            ttype = "Need to Buy" if urgency > 0.5 else "Desire to Buy"
            buy_candidates.append({
                "resource": r, "trade_type": ttype, "price": price,
                "quantity": qty, "_urgency": urgency + 10, "_source": "survival",
            })

        # --- District/city cost buy: resources needed to build planned target ---
        # Only buy if production won't cover the shortfall by next session
        if district_plan and r in district_plan.get("cost", {}) and r != "money":
            needed = district_plan["cost"][r]
            next_session_stockpile = stockpile + max(0, net)
            if next_session_stockpile < needed:
                shortfall = needed - next_session_stockpile
                price = int(round(mkt * 1.10 / 5)) * 5
                qty = min(5, max(1, int(shortfall)))
                buy_candidates.append({
                    "resource": r, "trade_type": "Desire to Buy", "price": price,
                    "quantity": qty, "_urgency": 7, "_source": "goal",
                })

        # --- Sell: overflow resources (would exceed stockpile cap) ---
        cap = state.get("resource_capacity", {}).get(r, 0)
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
        if not is_luxury and sessions == float("inf") and stockpile > 20:
            if r in goal_resources:
                continue
            surplus_sessions = stockpile / max(net, 0.01) if net > 0 else 100
            if surplus_sessions > 6:
                sell_mult = 0.90 * sell_bias
                if goal_type == "expand_trade":
                    sell_mult = 0.85 * sell_bias
                price = int(round(mkt * sell_mult / 5)) * 5
                price = max(price, int(mkt * 0.70 / 5) * 5)
                qty = min(5, max(1, int(net * 1.5)))
                ttype = "Need to Sell" if surplus_sessions > 12 else "Desire to Sell"
                source = "goal" if goal_type == "expand_trade" else "opportunistic"
                sell_candidates.append({
                    "resource": r, "trade_type": ttype, "price": price,
                    "quantity": qty, "_urgency": surplus_sessions, "_source": source,
                })

    # Deduplicate buy candidates per resource (keep highest urgency)
    seen_buys = {}
    for entry in buy_candidates:
        r = entry["resource"]
        if r not in seen_buys or entry["_urgency"] > seen_buys[r]["_urgency"]:
            seen_buys[r] = entry
    buy_candidates = sorted(seen_buys.values(), key=lambda x: x["_urgency"], reverse=True)
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
        need_weights  = compute_need_weights(state, market_prices)

        # --- Step 1: Upkeep floor ---
        upkeep_assignments, remaining_pops, projected_net, upkeep_log, upkeep_ratio = \
            compute_upkeep_floor(state, market_prices)
        log.extend(upkeep_log)

        # --- Step 2: Strategic goal ---
        goal, goal_candidates = select_strategic_goal(
            old_nation, state, personality, upkeep_ratio, market_prices
        )
        log.append(f"Strategic goal: {goal['display_name']} (score {goal['score']})")
        log.append(f"  Rationale: {goal['rationale']}")

        # --- Step 3: Goal-aware district ---
        district_plan, district_scores, district_log = evaluate_goal_district(
            old_nation, new_nation, state, goal, need_weights,
            market_prices, upkeep_assignments, log
        )

        # --- Step 4: Goal-driven job assignment ---
        goal_assignments, goal_job_log, final_projected_net = assign_goal_jobs(
            state, goal, remaining_pops, projected_net,
            district_plan, market_prices
        )
        log.extend(goal_job_log)

        # Merge all assignments onto new_nation
        all_assignments = dict(upkeep_assignments)
        for jk, cnt in goal_assignments.items():
            all_assignments[jk] = all_assignments.get(jk, 0) + cnt
        existing_jobs = dict(new_nation.get("jobs", {}))
        for jk, cnt in all_assignments.items():
            existing_jobs[jk] = existing_jobs.get(jk, 0) + cnt
        new_nation["jobs"] = existing_jobs

        # --- Step 4b: Tech target ---
        tech_target = select_tech_target(old_nation, new_nation, state, goal, personality)
        if tech_target:
            log.append(f"Tech target: {tech_target['display_name']} ({tech_target['rationale']})")

        # --- Step 5: Goal-aware trade (uses final projected_net including goal jobs) ---
        desires = generate_goal_trade_desires(
            state, goal, personality, district_plan, final_projected_net, market_prices
        )
        new_nation["resource_desires"] = desires
        for d in desires:
            src = f" [{d.get('source', '')}]" if d.get("source") else ""
            log.append(f"{d['trade_type']} {d['resource']} ×{d['quantity']} @ {d['price']}{src}")

        # --- Persist AI state ---
        new_nation["ai_state"] = {
            "strategic_goal":     goal,
            "goal_candidates":    goal_candidates,
            "upkeep_assignments": upkeep_assignments,
            "upkeep_ratio":       round(upkeep_ratio, 2),
            "goal_assignments":   goal_assignments,
            "tech_target":        tech_target,
            "planned_district":   district_plan,
            "decision_log":       log,
        }

    except Exception as e:
        import traceback
        new_nation["ai_state"] = {"decision_log": [f"AI error: {e}", traceback.format_exc()]}

    name = old_nation.get("name", "Unknown")
    return f"{name}: AI decision ({len(log)} actions)\n"


# ---------------------------------------------------------------------------
# Mid-session AI market matching (NATION_CROSS_TICK_FUNCTION)
# ---------------------------------------------------------------------------

def ai_market_matching_tick(old_nations, new_nations, schema):
    """
    Auto-match unfilled AI buy/sell orders within each market.
    Runs as a separate admin action partway through the session,
    after players have had a window to fill AI orders first.

    Prioritises: critical buys first, then desire-buys; price must overlap.
    Trades execute at the seller's ask price.
    """
    log_lines = []

    try:
        market_links_db = category_data["market_links"]["database"]
        markets         = list(mongo.db.markets.find({}, {"_id": 1, "name": 1}))

        # Index new_nations by string id for fast lookup
        nation_idx = {str(n.get("_id", "")): i for i, n in enumerate(new_nations)}

        for market in markets:
            market_id  = str(market["_id"])
            mkt_name   = market.get("name", market_id)
            member_links = list(market_links_db.find({"market": market_id}, {"member": 1}))
            member_ids   = {lnk["member"] for lnk in member_links}

            # Gather buy/sell orders from NPC nations in this market
            buy_orders  = []
            sell_orders = []

            for mid in member_ids:
                idx = nation_idx.get(mid)
                if idx is None:
                    continue
                nation = new_nations[idx]
                if nation.get("temperament", "Player") == "Player":
                    continue
                for didx, desire in enumerate(nation.get("resource_desires", [])):
                    qty = desire.get("quantity", 0)
                    if qty <= 0:
                        continue
                    entry = {
                        "resource":   desire["resource"],
                        "trade_type": desire["trade_type"],
                        "price":      desire.get("price", 0),
                        "quantity":   qty,
                        "nation_idx": idx,
                        "desire_idx": didx,
                    }
                    if "Buy" in desire["trade_type"]:
                        buy_orders.append(entry)
                    elif "Sell" in desire["trade_type"]:
                        sell_orders.append(entry)

            # Sort: buyers by price desc (highest bidder first), sellers by price asc
            buy_orders.sort(key=lambda x: (
                0 if x["trade_type"] == "Need to Buy" else 1, -x["price"]
            ))
            sell_orders.sort(key=lambda x: (
                0 if x["trade_type"] == "Need to Sell" else 1, x["price"]
            ))

            for buy in buy_orders:
                for sell in sell_orders:
                    if buy["resource"] != sell["resource"]:
                        continue
                    if buy["nation_idx"] == sell["nation_idx"]:
                        continue
                    if buy["price"] < sell["price"]:
                        continue
                    if sell["quantity"] <= 0 or buy["quantity"] <= 0:
                        continue

                    qty      = min(buy["quantity"], sell["quantity"])
                    price    = sell["price"]
                    total    = qty * price
                    resource = buy["resource"]

                    buyer  = new_nations[buy["nation_idx"]]
                    seller = new_nations[sell["nation_idx"]]

                    # Execute transfer
                    b_storage = buyer.setdefault("resource_storage", {})
                    s_storage = seller.setdefault("resource_storage", {})
                    b_storage[resource] = b_storage.get(resource, 0) + qty
                    s_storage[resource] = s_storage.get(resource, 0) - qty
                    buyer["money"]  = buyer.get("money", 0) - total
                    seller["money"] = seller.get("money", 0) + total

                    # Update desire quantities
                    buy["quantity"]  -= qty
                    sell["quantity"] -= qty
                    buyer["resource_desires"][buy["desire_idx"]]["quantity"]   = buy["quantity"]
                    seller["resource_desires"][sell["desire_idx"]]["quantity"] = sell["quantity"]

                    log_lines.append(
                        f"[{mkt_name}] {buyer.get('name','?')} bought {qty}× {resource} "
                        f"from {seller.get('name','?')} @ {price} (total {total})"
                    )

    except Exception as e:
        log_lines.append(f"AI market matching error: {e}")

    return "\n".join(log_lines) + "\n" if log_lines else ""
