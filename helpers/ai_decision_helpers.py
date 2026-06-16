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
    current_district_cnt = len(old_nation.get("districts", []))
    open_district_slots  = max(0, district_slots - current_district_cnt)

    # District cost modifier (e.g. -0.3 for Industrious race)
    district_cost_mod = old_nation.get("district_cost", 0)

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
        "existing_def_keys":     existing_def_keys,
        "existing_types":        existing_types,
        "open_district_slots":   open_district_slots,
        "district_cost_mod":     district_cost_mod,
        "trade_speed":           old_nation.get("trade_speed", 7),
    }


# ---------------------------------------------------------------------------
# Need weights
# ---------------------------------------------------------------------------

def _weights_from_net(net_production, stockpiles, prices, money_income):
    """
    Core weight computation from a net-production snapshot.
    Called by compute_need_weights and by assign_ai_jobs after each pop assignment.

    `prices` should be dynamic market prices (from get_stored_market_prices) or
    static base prices as a fallback.  Surplus floor is 0.3 + price/100.
    """
    weights = {}
    for r, net in net_production.items():
        stockpile = stockpiles.get(r, 0)
        if net < 0 and stockpile > 0:
            sessions = stockpile / (-net)
        elif net < 0:
            sessions = 0.0
        else:
            sessions = float("inf")

        price = prices.get(r, 10)
        if sessions < 2:
            w = 5.0
        elif sessions < 4:
            w = 3.0
        elif net < 0:
            surplus_w = 0.3 + price / 100.0
            w = surplus_w + (2.0 - surplus_w) * (4.0 / sessions)
        elif stockpile < 5:
            w = 1.3
        else:
            w = 0.3 + price / 100.0
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
    )


# ---------------------------------------------------------------------------
# Job scoring
# ---------------------------------------------------------------------------

def score_jobs(state, need_weights):
    """
    Compute efficiency score for each available job.
    Factors in: resource need weights, district efficiency bonus, upkeep cost.
    """
    nation_districts = state["existing_def_keys"] | state["existing_types"]
    scores = {}

    for jk, job in state["available_jobs"].items():
        prod_value  = 0.0
        upkeep_cost = 0.0

        for field, amount in job.get("production", {}).items():
            if field in need_weights:
                prod_value += need_weights[field] * amount
            elif field in PRODUCTION_FIELD_MAP:
                stockpile_key, base_w = PRODUCTION_FIELD_MAP[field]
                if stockpile_key and stockpile_key in need_weights:
                    prod_value += need_weights[stockpile_key] * amount
                else:
                    prod_value += base_w * amount

        for field, amount in job.get("upkeep", {}).items():
            w = need_weights.get(field, 1.0)
            upkeep_cost += w * amount

        base = prod_value - upkeep_cost

        # District bonus scales with how urgently needed the primary output is.
        # Full 1.4× only when the primary resource is at critical weight (5.0);
        # drops to ~1.0 when the resource is already at surplus.
        district_bonus = 1.0
        req_districts  = job.get("requirements", {}).get("district", [])
        if req_districts and any(r in nation_districts for r in req_districts):
            primary_weight = 0.0
            best_prod_val  = 0.0
            for field, amount in job.get("production", {}).items():
                if field in need_weights and isinstance(amount, (int, float)) and amount > 0:
                    pv = need_weights[field] * amount
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
        job_scores = score_jobs(state, curr_weights)
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
    initial_scores = score_jobs(state, need_weights)
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
# Goal generation
# ---------------------------------------------------------------------------

GOAL_BASE_PRIORITY = {
    "fix_critical_deficit": 95,
    "fix_deficit":          72,
    "fix_weakness":         58,   # deficit not coverable by current jobs
    "sell_surplus":         48,
    "grow_economy":         38,
    "expand_military":      32,
    "expand_territory":     30,
    "research_tech":        24,
    "build_wonder":         14,
}


def generate_goals(old_nation, state, assignments, personality):
    """Return sorted list of {type, priority, rationale} dicts."""
    goals    = []
    add_goal = lambda gtype, rationale, adj=0: goals.append({
        "type":      gtype,
        "priority":  max(1, min(100, GOAL_BASE_PRIORITY[gtype] + adj)),
        "rationale": rationale,
    })

    # Resource deficits
    covered_by_jobs = set()
    for jk, cnt in assignments.items():
        if cnt > 0:
            for field in state["available_jobs"].get(jk, {}).get("production", {}):
                covered_by_jobs.add(field)

    for r, sessions in state["sessions_until_empty"].items():
        net = state["net_production"].get(r, 0)
        if sessions < 2:
            add_goal("fix_critical_deficit",
                     f"{r} stockpile empty in {sessions:.1f} sessions (net {net:+.1f}/tick)",
                     adj=int((2 - sessions) * 5))
        elif net < 0:
            if r not in covered_by_jobs:
                add_goal("fix_weakness",
                         f"{r} in deficit and no assigned job covers it (net {net:+.1f}/tick)")
            else:
                add_goal("fix_deficit",
                         f"{r} net {net:+.1f}/tick, {sessions:.1f} sessions buffer")

    # Surplus sell opportunity
    base_prices  = _base_prices()
    large_surplus = [
        r for r, s in state["sessions_until_empty"].items()
        if s == float("inf") and state["stockpiles"].get(r, 0) > 20
    ]
    if large_surplus and state["trade_slots"] > 0:
        names = ", ".join(large_surplus[:3])
        add_goal("sell_surplus", f"Large surplus in {names}",
                 adj=int(personality.get("trade", 0) * 15))

    # Economy stable → grow
    deficit_count = sum(1 for r in state["net_production"] if state["net_production"][r] < 0)
    if deficit_count == 0:
        add_goal("grow_economy", "All resources stable",
                 adj=int(personality.get("economic", 0) * 20))

    # Military expansion
    aggr = personality.get("aggression", 0)
    mil  = personality.get("military", 0)
    if aggr > 0 or mil > 0:
        add_goal("expand_military", "Aggression/military personality active",
                 adj=int((aggr + mil) * 20))

    # Territory expansion
    pop_cap = old_nation.get("effective_pop_capacity", 0)
    pops    = state["total_pops"]
    if pops > pop_cap * 0.8 and personality.get("expansion", 0) > -0.2:
        add_goal("expand_territory",
                 f"Pop count {pops} approaching capacity {pop_cap}",
                 adj=int(personality.get("expansion", 0) * 20))

    # Tech research (only if economy stable for a while)
    if deficit_count == 0 and state["money_income"] > 0:
        add_goal("research_tech", "Economy stable, money positive",
                 adj=int(personality.get("economic", 0) * 15))

    goals.sort(key=lambda g: g["priority"], reverse=True)
    return goals


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


def _district_modifier_value(modifiers_list, need_weights):
    """Sum the weighted value of a list of modifier objects against current needs."""
    from calculations.source_adapters import _resolve_modifier_type
    total = 0.0
    for m in modifiers_list or []:
        field = _resolve_modifier_type(m)
        value = m.get("value", 0)
        # Map field to a stockpile key if needed
        if field in need_weights:
            total += need_weights[field] * value
        elif field in PRODUCTION_FIELD_MAP:
            _, base_w = PRODUCTION_FIELD_MAP[field]
            total += base_w * value
    return total


def score_buildable_districts(old_nation, state, need_weights, market_buy_prices):
    """
    Score all districts the nation could build.
    Returns list of (score, def_key_or_type, display_name, actual_cost, rationale).
    DB-driven districts take priority; legacy JSON districts also considered.
    """
    if state["open_district_slots"] <= 0:
        return []

    results = []

    # --- DB-driven district defs ---
    try:
        defs = list(mongo.db.district_defs.find({}, {"_id": 0}))
    except Exception:
        defs = []

    for dd in defs:
        dk = dd.get("key", "")
        if not dk or dk in state["existing_def_keys"]:
            continue

        raw_cost    = dd.get("cost", {})
        actual_cost = _apply_district_cost_mod(raw_cost, state["district_cost_mod"])

        # Check nation can pay (rough check; money handled separately)
        can_afford_now = all(
            state["stockpiles"].get(r, 0) >= amt
            for r, amt in actual_cost.items()
            if r != "money"
        ) and state["money"] >= actual_cost.get("money", 0)

        mod_value    = _district_modifier_value(dd.get("modifiers", []), need_weights)
        market_bonus = sum(
            need_weights.get(r, 0.5) * v
            for r, v in market_buy_prices.items()
            if any(
                m.get("value", 0) > 0 and
                _try_resolve_field(m) == r
                for m in dd.get("modifiers", [])
            )
        ) * 0.3  # discount: market opportunity is a bonus, not the primary driver

        cost_penalty = sum(
            need_weights.get(r, 1.0) * amt
            for r, amt in actual_cost.items()
            if r != "money"
        ) * 0.15 + (actual_cost.get("money", 0) / max(state["money"] + 1, 1)) * 5

        score = mod_value + market_bonus - cost_penalty
        if score <= 0:
            continue

        rationale = (
            f"{dd.get('display_name', dk)}: modifier value {mod_value:.1f}"
            + (f", market opportunity +{market_bonus:.1f}" if market_bonus > 0.5 else "")
            + (f" [can afford]" if can_afford_now else f" [saving]")
        )
        results.append((score, dk, dd.get("display_name", dk), actual_cost, rationale, "db"))

    results.sort(key=lambda x: x[0], reverse=True)
    return results


def _try_resolve_field(m):
    """Safe wrapper for _resolve_modifier_type."""
    try:
        from calculations.source_adapters import _resolve_modifier_type
        return _resolve_modifier_type(m)
    except Exception:
        return ""


def update_district_plan(old_nation, new_nation, state, goals, personality, market_buy_prices, need_weights, log):
    """
    If the nation has an existing plan, check if it's still best and affordable.
    Otherwise pick the top-scored district as the new plan.
    Builds immediately if affordable.
    Returns updated plan dict (or None) and appends to log.
    """
    current_plan = old_nation.get("ai_state", {}).get("planned_district")
    scored       = score_buildable_districts(old_nation, state, need_weights, market_buy_prices)

    if not scored:
        return None

    best_score, best_key, best_name, best_cost, best_rationale, best_src = scored[0]

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
# Main AI tick
# ---------------------------------------------------------------------------

def ai_decision_tick(old_nation, new_nation, schema):
    """
    Main per-nation AI tick. Replaces ai_resource_desire_tick.
    Must run AFTER nation_job_cleanup_tick in NATION_TICK_FUNCTIONS.
    """
    if old_nation.get("temperament", "Player") == "Player":
        return ""

    log = []

    try:
        personality   = get_ai_personality(old_nation)
        state         = evaluate_nation_state(old_nation)
        market_prices = get_stored_market_prices(old_nation)
        need_weights  = compute_need_weights(state, market_prices)

        # --- Job assignment ---
        assignments, job_log = assign_ai_jobs(state, need_weights, market_prices)
        log.extend(job_log)

        # Merge with persistent jobs
        existing_jobs = dict(new_nation.get("jobs", {}))
        for jk, cnt in assignments.items():
            existing_jobs[jk] = existing_jobs.get(jk, 0) + cnt
        new_nation["jobs"] = existing_jobs

        # --- Goals ---
        goals = generate_goals(old_nation, state, assignments, personality)

        # --- District planning ---
        district_plan = update_district_plan(
            old_nation, new_nation, state, goals, personality,
            market_prices, need_weights, log
        )

        # --- Resource desires ---
        desires = generate_resource_desires(state, goals, personality, market_prices)
        new_nation["resource_desires"] = desires
        for d in desires:
            log.append(f"{d['trade_type']} {d['resource']} ×{d['quantity']} @ {d['price']}")

        # --- Persist AI state ---
        new_nation["ai_state"] = {
            "goals":             goals,
            "planned_district":  district_plan,
            "decision_log":      log,
        }

    except Exception as e:
        new_nation["ai_state"] = {"decision_log": [f"AI error: {e}"]}

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
