"""
Mech RP simulation framework for AI nations.

Mirrors the manual player process (RP post -> quality rating -> most relevant
character stat -> d20 roll -> outcome) with a data-driven library of "mech RP"
definitions (the mrp_defs collection, admin-editable like wonders/traits) that
an AI nation selects from based on its already-computed strategic goal (see
ai_decision_helpers.select_strategic_goal) and its concrete situational needs.

Resolution formula (see _resolve_roll):
    d20 (1-20) + rp_bonus (0-5, simulates the "RP is rated" step) +
    min(stat_value + nation_modifiers, 15) vs. a per-definition DC.
A natural 1 is always a Critical Failure; a natural 20 is always a Critical
Success; otherwise the margin over the DC buckets into five outcome tiers.

Runs as its own tick ("AI Mech RP Tick"), registered in
tick_helpers.NATION_TICK_FUNCTIONS immediately after "AI Decision Tick" so it
can read that tick's freshly-computed ai_state.strategic_goal/secondary_goal/
planned_district for the same nation within the same tick pass.
"""
import random
from copy import deepcopy

from bson import ObjectId

from app_core import mongo, category_data
from helpers.ai_decision_helpers import (
    evaluate_nation_state, get_ai_personality, compute_need_weights,
    get_stored_market_prices, select_strategic_goal,
    _nation_is_nomadic, _nation_in_active_war, PRODUCTION_FIELD_MAP,
)
from helpers.hex_map_helpers import select_passive_expansion_tiles

CHARACTER_STATS = ["rulership", "cunning", "charisma", "prowess", "magic", "strategy"]

# Culture/religion documents rate their pull with a "conversion_strength" enum
# (json-data/schemas/cultures.json, religions.json) that nothing previously
# consumed. Convert Population mech RPs use it to scale how many pops a
# successful attempt actually converts.
CONVERSION_STRENGTH_MULTIPLIER = {
    "None": 0.0,
    "Weak": 0.5,
    "Moderate": 1.0,
    "Strong": 1.5,
    "Intense": 2.0,
    "Zealous": 3.0,
}

TIER_ORDER = ["critical_failure", "failure", "partial_success", "success", "critical_success"]

# Rough "how good is this tier" weight used only for the AI's own expected-value
# estimate (not shown to players) — critical_failure isn't 0 because even a
# failed attempt this session isn't strictly worthless to consider relative to
# not attempting anything at all, but it's heavily discounted.
TIER_VALUE_WEIGHT = {
    "critical_failure": 0.0,
    "failure": 0.1,
    "partial_success": 0.5,
    "success": 1.0,
    "critical_success": 1.75,
}

MAX_ATTEMPTS_PER_SESSION = 5
NON_RP_BONUS_CAP = 15


# ---------------------------------------------------------------------------
# Requirement predicates
# ---------------------------------------------------------------------------

def _req_open_district_slot(old_nation, state):
    return state.get("open_district_slots", 0) > 0

def _req_has_planned_district(old_nation, state):
    return bool((old_nation.get("ai_state") or {}).get("planned_district"))

def _req_not_nomadic(old_nation, state):
    return not _nation_is_nomadic(old_nation)

def _req_stability_not_maxed(old_nation, state):
    return old_nation.get("stability_gain_chance", 0) < old_nation.get("stability_gain_chance_cap", 1.0)

def _req_high_stability_loss_chance(old_nation, state):
    return old_nation.get("stability_loss_chance", 0) >= 0.5

def _req_in_active_war(old_nation, state):
    return _nation_in_active_war(old_nation)

def _req_has_available_tech_target(old_nation, state):
    return bool((old_nation.get("ai_state") or {}).get("tech_target"))

REQUIREMENT_CHECKS = {
    "open_district_slot": _req_open_district_slot,
    "has_planned_district": _req_has_planned_district,
    "not_nomadic": _req_not_nomadic,
    "stability_not_maxed": _req_stability_not_maxed,
    "high_stability_loss_chance": _req_high_stability_loss_chance,
    "in_active_war": _req_in_active_war,
    "has_available_tech_target": _req_has_available_tech_target,
}


def _check_requirements(mrp_def, old_nation, state):
    for req in mrp_def.get("requirements", []):
        check = REQUIREMENT_CHECKS.get(req)
        if check and not check(old_nation, state):
            return False
    return True


# ---------------------------------------------------------------------------
# Roll resolution
# ---------------------------------------------------------------------------

def _tier_for_roll(d20, margin):
    """Bucket a resolved roll into an outcome tier. A natural 1 is always a
    Critical Failure and a natural 20 is always a Critical Success,
    regardless of margin; otherwise bucket by margin over the DC."""
    if d20 == 1:
        return "critical_failure"
    if d20 == 20:
        return "critical_success"
    if margin >= 10:
        return "critical_success"
    if margin >= 0:
        return "success"
    if margin >= -5:
        return "partial_success"
    if margin >= -10:
        return "failure"
    return "critical_failure"


def _resolve_roll(mrp_def, character, difficulty, modifier_total):
    """Perform one full mech RP roll. Returns a dict with every component of
    the resolution, for logging/debugging and for effect magnitude lookup."""
    relevant_stats = mrp_def.get("relevant_stats") or CHARACTER_STATS
    stat_used = random.choice(relevant_stats)
    stat_value = (character or {}).get(stat_used, 0)

    d20 = random.randint(1, 20)
    rp_bonus = random.randint(0, 5)
    non_rp_bonus = min(stat_value + modifier_total, NON_RP_BONUS_CAP)
    total = d20 + rp_bonus + non_rp_bonus
    margin = total - difficulty
    tier = _tier_for_roll(d20, margin)

    return {
        "stat_used": stat_used,
        "stat_value": stat_value,
        "roll": d20,
        "rp_bonus": rp_bonus,
        "modifier_total": modifier_total,
        "non_rp_bonus": non_rp_bonus,
        "difficulty": difficulty,
        "total": total,
        "margin": margin,
        "outcome_tier": tier,
    }


def _estimate_mrp_odds(mrp_def, character, difficulty, modifier_total):
    """Exact tier-probability distribution for this MRP, given the acting
    character and current modifier total. Three independent uniform random
    components (d20, random stat pick, rp_bonus) means a small, exact
    enumeration — no simulation needed."""
    relevant_stats = mrp_def.get("relevant_stats") or CHARACTER_STATS
    counts = {t: 0 for t in TIER_ORDER}
    total_combos = 0
    for stat in relevant_stats:
        stat_value = (character or {}).get(stat, 0)
        non_rp_bonus = min(stat_value + modifier_total, NON_RP_BONUS_CAP)
        for d20 in range(1, 21):
            for rp_bonus in range(0, 6):
                total = d20 + rp_bonus + non_rp_bonus
                margin = total - difficulty
                tier = _tier_for_roll(d20, margin)
                counts[tier] += 1
                total_combos += 1
    if total_combos == 0:
        return {t: 0.0 for t in TIER_ORDER}
    return {t: counts[t] / total_combos for t in TIER_ORDER}


def _expected_value(odds):
    return sum(odds.get(t, 0.0) * TIER_VALUE_WEIGHT[t] for t in TIER_ORDER)


def _modifier_total_for(mrp_key, nation):
    bonuses = nation.get("mrp_bonuses") or {}
    return bonuses.get(f"mrp_{mrp_key}_bonus", 0) + bonuses.get("mrp_all_bonus", 0)


# ---------------------------------------------------------------------------
# Effect handlers
# ---------------------------------------------------------------------------

def _grant_specific_resource(old_nation, new_nation, resource_key, amount, label):
    """Grant `amount` units of exactly `resource_key`, added directly to
    storage and deliberately NOT capped at nation_resource_capacity — same
    reasoning as _grant_resource_windfall: give the nation a session to spend
    the overflow before Nation Income Tick reapplies the cap next session."""
    if amount <= 0 or not resource_key:
        return ""
    storage = new_nation.get("resource_storage") or {}
    storage[resource_key] = storage.get(resource_key, 0) + amount
    new_nation["resource_storage"] = storage
    name = old_nation.get("name", "Unknown")
    return f"{name} gained {amount} {resource_key} from {label}.\n"


def _apply_grant_resources(old_nation, new_nation, schema, mrp_def, magnitude, target_resource, need_weights, state, dry_run=False, acting_character=None, stat_used=None, conversion_target=None):
    if magnitude <= 0 or not target_resource:
        return ""
    return _grant_specific_resource(
        old_nation, new_nation, target_resource, int(magnitude),
        f"a mech RP ({mrp_def.get('name', mrp_def.get('key', '?'))})"
    )


def _apply_adjust_stability(old_nation, new_nation, schema, mrp_def, magnitude, target_resource, need_weights, state, dry_run=False, acting_character=None, stat_used=None, conversion_target=None):
    if magnitude == 0:
        return ""
    from helpers.tick_helpers import adjust_stability
    label = f"mech RP: {mrp_def.get('name', mrp_def.get('key', '?'))}"
    return adjust_stability(old_nation, new_nation, schema, [int(magnitude)], [label])


def _apply_expand_territory(old_nation, new_nation, schema, mrp_def, magnitude, target_resource, need_weights, state, dry_run=False, acting_character=None, stat_used=None, conversion_target=None):
    """dry_run=True simulates tile selection (for the read-only AI Goals
    Preview tool) WITHOUT writing to hex_map_tiles/nations or bumping the map
    version — mirrors the dry_run convention already used by
    ai_decision_helpers.evaluate_goal_district for the same reason."""
    tiles_wanted = int(magnitude)
    if tiles_wanted <= 0:
        return ""
    nation_name = old_nation.get("name", "Unknown")
    all_tiles = list(mongo.db.hex_map_tiles.find(
        {},
        {"q": 1, "r": 1, "terrain": 1, "owner": 1,
         "city": 1, "district": 1, "wonder": 1, "capital": 1,
         "portal": 1, "route": 1, "_id": 0},
    ))
    tile_map = {(t["q"], t["r"]): t for t in all_tiles}
    claimed = []
    for _ in range(tiles_wanted):
        to_claim = select_passive_expansion_tiles(old_nation, all_tiles)
        if not to_claim:
            break
        for (q, r) in to_claim:
            if not dry_run:
                mongo.db.hex_map_tiles.update_one({"q": q, "r": r}, {"$set": {"owner": nation_name}})
            claimed.append((q, r))
            if (q, r) in tile_map:
                tile_map[(q, r)]["owner"] = nation_name
    if not claimed:
        return ""
    if not dry_run:
        pipeline = [
            {"$match": {"owner": nation_name, "terrain": {"$exists": True, "$ne": None}}},
            {"$group": {"_id": "$terrain", "count": {"$sum": 1}}},
        ]
        counts = {doc["_id"]: doc["count"] for doc in mongo.db.hex_map_tiles.aggregate(pipeline)}
        mongo.db.nations.update_one({"name": nation_name}, {"$set": {"territory_types": counts}})
        from helpers.hex_map_helpers import bump_tile_version
        bump_tile_version()
    verb = "would expand" if dry_run else "expanded"
    return f"{nation_name} {verb} into {len(claimed)} tile(s) from a mech RP ({mrp_def.get('name', mrp_def.get('key', '?'))}).\n"


def _apply_gain_money(old_nation, new_nation, schema, mrp_def, magnitude, target_resource, need_weights, state, dry_run=False, acting_character=None, stat_used=None, conversion_target=None):
    amount = int(magnitude) * 50
    if amount == 0:
        return ""
    new_nation["money"] = new_nation.get("money", old_nation.get("money", 0)) + amount
    name = old_nation.get("name", "Unknown")
    return f"{name} gained {amount} gold from a mech RP ({mrp_def.get('name', mrp_def.get('key', '?'))}).\n"


def _apply_grant_temp_modifier(old_nation, new_nation, schema, mrp_def, magnitude, target_resource, need_weights, state, dry_run=False, acting_character=None, stat_used=None, conversion_target=None):
    if magnitude == 0:
        return ""
    import uuid
    modifiers = list(new_nation.get("modifiers") or [])
    modifiers.append({
        "_id": uuid.uuid4().hex[:8],
        "field": f"mrp_{mrp_def.get('key', '')}_outcome",
        "value": magnitude,
        "duration": 3,
        "source": f"Mech RP: {mrp_def.get('name', mrp_def.get('key', '?'))}",
    })
    new_nation["modifiers"] = modifiers
    name = old_nation.get("name", "Unknown")
    return f"{name} gained a temporary modifier from a mech RP ({mrp_def.get('name', mrp_def.get('key', '?'))}).\n"


def _score_law_value(law_dict, need_weights):
    """Generic value of switching TO a law value with these flat modifier
    fields, using the same need-weight philosophy the rest of the AI uses for
    resources and abstract fields (PRODUCTION_FIELD_MAP). Scaled _modifiers
    entries are skipped for this quick heuristic."""
    score = 0.0
    for field, value in law_dict.items():
        if not isinstance(value, (int, float)) or field.startswith("_"):
            continue
        if field in need_weights:
            score += need_weights[field] * value
        elif field in PRODUCTION_FIELD_MAP:
            score += PRODUCTION_FIELD_MAP[field][1] * value
        else:
            score += 0.3 * value
    return score


def _apply_change_law(old_nation, new_nation, schema, mrp_def, magnitude, target_resource, need_weights, state, dry_run=False, acting_character=None, stat_used=None, conversion_target=None):
    if magnitude <= 0:
        return ""
    axis = mrp_def.get("law_axis", "")
    axis_schema = schema.get("properties", {}).get(axis, {})
    law_values = axis_schema.get("laws", {})
    if not axis or not law_values:
        return ""

    current_value = old_nation.get(axis, "")
    current_law = law_values.get(current_value, {})
    current_score = _score_law_value(current_law, need_weights)

    has_infrastructure = bool(old_nation.get("districts")) or bool(old_nation.get("cities"))

    best_value, best_score = None, current_score
    for candidate_value, candidate_law in law_values.items():
        if candidate_value == current_value:
            continue
        # Guardrail: never let a mech RP flip a nation with built infrastructure
        # into a nomadic government type — that would orphan its districts/cities.
        if has_infrastructure and candidate_law.get("nomadic", 0) > 0:
            continue
        candidate_score = _score_law_value(candidate_law, need_weights)
        if candidate_score > best_score:
            best_value, best_score = candidate_value, candidate_score

    if best_value is None:
        return ""

    new_nation[axis] = best_value
    name = old_nation.get("name", "Unknown")
    axis_label = axis_schema.get("label", axis)
    return f"{name} changed its {axis_label} from {current_value} to {best_value} following a mech RP ({mrp_def.get('name', mrp_def.get('key', '?'))}).\n"


def _apply_character_training(old_nation, new_nation, schema, mrp_def, magnitude, target_resource, need_weights, state, dry_run=False, acting_character=None, stat_used=None, conversion_target=None):
    """Grant the acting character stat points directly, in the stat used for
    this roll — replaces the AI's old passive per-session stat gain, which
    used to roll independently for every stat and snowball far past players."""
    gain = int(magnitude)
    if gain == 0 or not acting_character or not stat_used:
        return ""
    name = acting_character.get("name", "Unknown")
    cap = acting_character.get(f"{stat_used}_cap", 4)
    current = acting_character.get(stat_used, 0)
    gain = min(gain, max(0, cap - current)) if gain > 0 else gain
    if gain == 0:
        return f"{name} is already at their {stat_used} cap — Character Training had no effect.\n"
    label = f"Mech RP: {mrp_def.get('name', mrp_def.get('key', '?'))}"
    if dry_run:
        return f"{name} would gain {gain} {stat_used} from a mech RP ({mrp_def.get('name', mrp_def.get('key', '?'))}).\n"
    import uuid
    from helpers.change_helpers import system_request_change, system_approve_change
    new_character = deepcopy(acting_character)
    modifiers = list(new_character.get("modifiers") or [])
    modifiers.append({
        "_id": uuid.uuid4().hex[:8],
        "modifier_type": "attribute",
        "attribute": stat_used,
        "value": gain,
        "duration": -1,
        "source": label,
    })
    new_character["modifiers"] = modifiers
    change_id = system_request_change(
        data_type="characters",
        item_id=acting_character["_id"],
        change_type="Update",
        before_data=acting_character,
        after_data=new_character,
        reason=f"Character Training mech RP for {name}",
    )
    system_approve_change(change_id)
    return f"{name} gained {gain} {stat_used} from a mech RP ({mrp_def.get('name', mrp_def.get('key', '?'))}).\n"


def _apply_heir_training(old_nation, new_nation, schema, mrp_def, magnitude, target_resource, need_weights, state, dry_run=False, acting_character=None, stat_used=None, conversion_target=None):
    """Bank stat points on the nation for whoever succeeds the current ruler.
    Only mutates the in-memory new_nation dict (like every handler besides
    expand_territory), so no dry_run handling is needed here — the actual
    apply-and-clear happens later in generate_ai_character
    (helpers/tick_helpers.py) the next time this nation generates a new ruler."""
    gain = int(magnitude)
    if gain == 0 or not stat_used:
        return ""
    name = old_nation.get("name", "Unknown")
    bonuses = dict(new_nation.get("heir_training_bonuses") or old_nation.get("heir_training_bonuses") or {})
    if gain < 0 and not bonuses.get(stat_used):
        return ""
    bonuses[stat_used] = max(0, bonuses.get(stat_used, 0) + gain)
    new_nation["heir_training_bonuses"] = bonuses
    if gain < 0:
        return f"{name} lost {-gain} banked {stat_used} of heir training from a mech RP ({mrp_def.get('name', mrp_def.get('key', '?'))}).\n"
    return f"{name} banked {gain} {stat_used} of heir training for its next ruler from a mech RP ({mrp_def.get('name', mrp_def.get('key', '?'))}).\n"


def _apply_convert_population(old_nation, new_nation, schema, mrp_def, magnitude, target_resource, need_weights, state, dry_run=False, acting_character=None, stat_used=None, conversion_target=None):
    """Convert pops (domestic or a nearby foreign nation's) to this nation's
    own primary culture/religion. Writes directly to the pops collection —
    like _apply_expand_territory's tile claims, this is a real, immediate DB
    write outside the normal new_nation diffing, so it must respect dry_run."""
    if magnitude <= 0 or not conversion_target:
        return ""
    field = mrp_def.get("conversion_field", "culture")
    own_value = old_nation.get(f"primary_{field}", "")
    if not own_value:
        return ""

    is_foreign = conversion_target.get("is_foreign")
    target_nation_id = conversion_target.get("target_nation_id")
    eligible_count = conversion_target.get("eligible_count", 0)
    strength_multiplier = conversion_target.get("strength_multiplier", 1.0)

    count = min(int(magnitude * strength_multiplier), eligible_count)
    if count <= 0:
        return ""

    nation_name = old_nation.get("name", "Unknown")
    label = mrp_def.get("name", mrp_def.get("key", "?"))

    if is_foreign:
        query_nation_id = target_nation_id
        try:
            target_doc = mongo.db.nations.find_one({"_id": ObjectId(target_nation_id)}, {"name": 1})
        except Exception:
            target_doc = None
        target_desc = f"pops in {target_doc['name']}" if target_doc else "foreign pops"
    else:
        query_nation_id = str(old_nation.get("_id", ""))
        target_desc = f"{nation_name}'s own pops"

    if dry_run:
        return f"{nation_name} would convert {count} {target_desc} to its {field} from a mech RP ({label}).\n"

    pop_ids = [
        p["_id"] for p in mongo.db.pops.find(
            {"nation": query_nation_id, field: {"$nin": [None, "", own_value]}},
            {"_id": 1},
        ).limit(count)
    ]
    if not pop_ids:
        return ""
    mongo.db.pops.update_many({"_id": {"$in": pop_ids}}, {"$set": {field: own_value}})
    return f"{nation_name} converted {len(pop_ids)} {target_desc} to its {field} from a mech RP ({label}).\n"


MRP_EFFECT_HANDLERS = {
    "grant_resources": _apply_grant_resources,
    "adjust_stability": _apply_adjust_stability,
    "expand_territory": _apply_expand_territory,
    "gain_money": _apply_gain_money,
    "grant_temp_modifier": _apply_grant_temp_modifier,
    "change_law": _apply_change_law,
    "character_training": _apply_character_training,
    "heir_training": _apply_heir_training,
    "convert_population": _apply_convert_population,
}


def _magnitude_for_tier(mrp_def, tier):
    return mrp_def.get(f"tier_magnitude_{tier}", 0) or 0


# ---------------------------------------------------------------------------
# Target selection: expand_territory (DC) and convert_population (DC + target)
# ---------------------------------------------------------------------------

def _expand_territory_difficulty(mrp_def, old_nation):
    """DC for an expand_territory attempt: harder the more tiles this
    definition is themed around claiming (tiles_requested), easier with more
    administration. tiles_requested only feeds the DC — the actual tiles
    claimed on success still follows tier_magnitude_* as before."""
    tiles_requested = mrp_def.get("tiles_requested", 1) or 1
    per_tile = mrp_def.get("difficulty_per_tile", 0) or 0
    admin_reduction = mrp_def.get("difficulty_reduction_per_admin", 0) or 0
    administration = old_nation.get("administration", 0) or 0
    difficulty = mrp_def.get("base_difficulty", 10) + tiles_requested * per_tile - administration * admin_reduction
    return max(1, difficulty)


def _best_foreign_conversion_target(old_nation, field, cache=None):
    """Nearby-nation scan for convert_population's foreign fallback —
    get_nations_within_distance does a full hex-map pass, so this memoizes
    per (nation, field) in the caller-supplied cache to avoid repeating that
    scan across multiple attempts/defs within one select_mech_rps call. Only
    depends on the acting nation and field, not on which mrp_def is asking."""
    if cache is not None and field in cache:
        return cache[field]
    try:
        from helpers.hex_map_helpers import get_nations_within_distance
        nearby_names = get_nations_within_distance(old_nation.get("name", ""), 5)
    except Exception:
        nearby_names = []
    own_value = old_nation.get(f"primary_{field}", "")
    best_target, best_count = None, 0
    for cand_name in nearby_names:
        cand = mongo.db.nations.find_one({"name": cand_name}, {"_id": 1})
        if not cand:
            continue
        cand_id = str(cand["_id"])
        count = mongo.db.pops.count_documents({
            "nation": cand_id, field: {"$nin": [None, "", own_value]},
        })
        if count > best_count:
            best_target, best_count = cand_id, count
    result = (best_target, best_count)
    if cache is not None:
        cache[field] = result
    return result


def _pick_conversion_target(mrp_def, old_nation, state, cache=None):
    """Choose what a convert_population attempt targets this session: pops
    within the nation's own borders (domestic, preferred — lower DC) or a
    nearby foreign nation's pops (foreign, only when no domestic candidates
    exist). Returns a dict with is_foreign/target_nation_id/eligible_count/
    difficulty/strength_multiplier, or None when nothing is convertible.

    `cache` (see _best_foreign_conversion_target) makes the expensive foreign
    scan a one-time cost per field for the whole select_mech_rps call instead
    of once per attempt per convert_population mech RP def."""
    field = mrp_def.get("conversion_field", "culture")
    own_value = old_nation.get(f"primary_{field}", "")
    if not own_value:
        return None

    collection_name = "cultures" if field == "culture" else "religions"
    try:
        own_doc = mongo.db[collection_name].find_one({"_id": ObjectId(str(own_value))}, {"conversion_strength": 1})
    except Exception:
        own_doc = None
    strength = (own_doc or {}).get("conversion_strength", "None")
    multiplier = CONVERSION_STRENGTH_MULTIPLIER.get(strength, 0.0)
    if multiplier <= 0:
        # Own culture/religion has no conversion pull — nothing to attempt.
        return None

    base_difficulty = mrp_def.get("base_difficulty", 10)
    nation_id = str(old_nation.get("_id", ""))
    domestic_count = mongo.db.pops.count_documents({
        "nation": nation_id, field: {"$nin": [None, "", own_value]},
    })
    if domestic_count > 0:
        return {
            "is_foreign": False, "target_nation_id": None,
            "eligible_count": domestic_count, "difficulty": base_difficulty,
            "strength_multiplier": multiplier,
        }

    foreign_modifier = mrp_def.get("foreign_conversion_difficulty_modifier", 0) or 0
    best_target, best_count = _best_foreign_conversion_target(old_nation, field, cache=cache)
    if not best_target:
        return None
    return {
        "is_foreign": True, "target_nation_id": best_target,
        "eligible_count": best_count, "difficulty": base_difficulty + foreign_modifier,
        "strength_multiplier": multiplier,
    }


# ---------------------------------------------------------------------------
# Target-resource selection (grant_resources only)
# ---------------------------------------------------------------------------

def _pick_target_resource(mrp_def, old_nation, state):
    """Which single resource a grant_resources MRP should target this
    attempt. Prefers a resource missing from the planned district/city's cost
    (the AI can't get resources elsewhere for what it's trying to build),
    else falls back to whichever listed resource has the worst deficit
    signal."""
    resource_diff = {
        item["resource"]: item.get("difficulty")
        for item in (mrp_def.get("resource_difficulty") or [])
        if item.get("resource")
    }
    if not resource_diff:
        return None, mrp_def.get("base_difficulty", 10)

    planned = (old_nation.get("ai_state") or {}).get("planned_district") or {}
    plan_cost = planned.get("cost", {})
    stockpiles = state.get("stockpiles", {})
    for r in resource_diff:
        if r == "money":
            continue
        needed = plan_cost.get(r, 0)
        if needed > 0 and stockpiles.get(r, 0) < needed:
            return r, resource_diff.get(r, mrp_def.get("base_difficulty", 10))

    net_production = state.get("net_production", {})
    worst_resource, worst_sessions = None, float("inf")
    for r in resource_diff:
        net = net_production.get(r, 0)
        stock = stockpiles.get(r, 0)
        if net < 0:
            sessions = stock / (-net) if stock > 0 else 0.0
        else:
            sessions = float("inf")
        if sessions < worst_sessions:
            worst_sessions = sessions
            worst_resource = r

    if worst_resource is None:
        worst_resource = next(iter(resource_diff))
    return worst_resource, resource_diff.get(worst_resource, mrp_def.get("base_difficulty", 10))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _goal_affinity_score(mrp_def, goal_type, secondary_goal_type):
    score = 0.0
    for entry in mrp_def.get("goal_affinity", []):
        weight = entry.get("weight", 0)
        if entry.get("goal_type") == goal_type:
            score += weight
        elif secondary_goal_type and entry.get("goal_type") == secondary_goal_type:
            score += weight * 0.5
    return score


def _situational_score(mrp_def, old_nation, target_resource):
    effect_type = mrp_def.get("effect_type", "")
    score = 0.0
    if effect_type == "adjust_stability":
        stability = old_nation.get("stability", "Balanced")
        if stability == "Fragile":
            score += 20
        elif stability == "Unsettled":
            score += 10
    elif effect_type == "change_law":
        loss_chance = old_nation.get("stability_loss_chance", 0)
        if loss_chance >= 0.5:
            score += (loss_chance - 0.5) * 20
    elif effect_type == "grant_resources" and target_resource:
        planned = (old_nation.get("ai_state") or {}).get("planned_district") or {}
        if target_resource in planned.get("cost", {}):
            score += 10
    return score


# ---------------------------------------------------------------------------
# Main selection + resolution loop
# ---------------------------------------------------------------------------

def _pick_acting_character(characters, relevant_stats):
    if not characters:
        return None
    if len(characters) == 1:
        return characters[0]
    stats = relevant_stats or CHARACTER_STATS
    return max(characters, key=lambda c: sum(c.get(s, 0) for s in stats))


def select_mech_rps(old_nation, new_nation, state, goal, secondary_goal, personality, schema, market_prices=None, dry_run=False):
    """Select and resolve up to MAX_ATTEMPTS_PER_SESSION mech RPs for this
    nation this session, mutating new_nation in place with their effects.
    Returns (attempts_log_list, log_lines) — attempts_log_list is the
    persisted ai_state.mech_rps shape; log_lines is a list of human-readable
    strings for the tick summary.

    dry_run=True skips the real, immediate DB writes made outside the normal
    new_nation diffing — expand_territory's tile claims, character_training's
    character update, and convert_population's pop conversions — used by the
    read-only AI Goals Preview tool so viewing a preview never mutates live
    data. Every other effect handler only ever mutates the passed-in
    new_nation dict, so they're already preview-safe without needing dry_run.
    """
    nation_id = str(old_nation.get("_id", ""))
    characters = list(mongo.db.characters.find(
        {"ruling_nation_org": nation_id, "health_status": {"$ne": "Dead"}}
    ))
    if not characters:
        return [], [f"{old_nation.get('name', 'Unknown')}: no eligible character for mech RPs.\n"]

    mrp_defs = list(mongo.db.mrp_defs.find({"active": True}))
    if not mrp_defs:
        return [], []

    goal_type = (goal or {}).get("type", "")
    secondary_goal_type = (secondary_goal or {}).get("type", "")
    cooldowns = dict(old_nation.get("mrp_cooldowns") or {})
    current_session = state.get("_current_session", 0)

    need_weights = compute_need_weights(state, market_prices)

    attempts = []
    log_lines = []
    pops_used_cooldowns = dict(cooldowns)
    foreign_conversion_cache = {}

    for _attempt in range(MAX_ATTEMPTS_PER_SESSION):
        candidates = []
        for mrp_def in mrp_defs:
            key = mrp_def.get("key", "")
            if not key:
                continue
            cooldown_sessions = mrp_def.get("cooldown_sessions", 0) or 0
            last_attempted = pops_used_cooldowns.get(key)
            if cooldown_sessions and last_attempted is not None and (current_session - last_attempted) < cooldown_sessions:
                continue
            if not _check_requirements(mrp_def, old_nation, state):
                continue

            target_resource, difficulty = (None, mrp_def.get("base_difficulty", 10))
            conversion_target = None
            effect_type = mrp_def.get("effect_type", "")
            if effect_type == "grant_resources":
                target_resource, difficulty = _pick_target_resource(mrp_def, old_nation, state)
                if target_resource is None:
                    continue
            elif effect_type == "expand_territory":
                difficulty = _expand_territory_difficulty(mrp_def, old_nation)
            elif effect_type == "convert_population":
                conversion_target = _pick_conversion_target(mrp_def, old_nation, state, cache=foreign_conversion_cache)
                if conversion_target is None:
                    continue
                difficulty = conversion_target["difficulty"]

            acting_character = _pick_acting_character(characters, mrp_def.get("relevant_stats"))
            modifier_total = _modifier_total_for(key, old_nation)
            odds = _estimate_mrp_odds(mrp_def, acting_character, difficulty, modifier_total)
            ev = _expected_value(odds)

            score = (
                _goal_affinity_score(mrp_def, goal_type, secondary_goal_type)
                + _situational_score(mrp_def, old_nation, target_resource)
                + ev * 5.0
            )
            if score <= 0:
                continue

            candidates.append((score, mrp_def, acting_character, target_resource, difficulty, modifier_total, conversion_target))

        if not candidates:
            break

        candidates.sort(key=lambda c: c[0], reverse=True)
        _, mrp_def, acting_character, target_resource, difficulty, modifier_total, conversion_target = candidates[0]
        key = mrp_def.get("key", "")

        result = _resolve_roll(mrp_def, acting_character, difficulty, modifier_total)
        magnitude = _magnitude_for_tier(mrp_def, result["outcome_tier"])

        handler = MRP_EFFECT_HANDLERS.get(mrp_def.get("effect_type", ""))
        effect_summary = ""
        if handler:
            effect_summary = handler(
                old_nation, new_nation, schema, mrp_def, magnitude, target_resource, need_weights, state,
                dry_run=dry_run, acting_character=acting_character, stat_used=result["stat_used"],
                conversion_target=conversion_target,
            ) or ""

        # Keep local state roughly current so the next attempt this session
        # sees the effects of this one (e.g. don't re-target the same
        # now-satisfied resource, don't re-pick a maxed-out stability MRP).
        if mrp_def.get("effect_type") == "grant_resources" and target_resource:
            state.setdefault("stockpiles", {})[target_resource] = state.get("stockpiles", {}).get(target_resource, 0) + max(0, magnitude)
        elif mrp_def.get("effect_type") == "adjust_stability" and magnitude:
            old_nation["stability_loss_chance"] = max(0.0, old_nation.get("stability_loss_chance", 0) - max(0, magnitude)) if magnitude > 0 else old_nation.get("stability_loss_chance", 0)

        entry = {
            "mrp_key": key,
            "display_name": mrp_def.get("name", key),
            "character_name": (acting_character or {}).get("name", "Unknown"),
            "stat_used": result["stat_used"],
            "stat_value": result["stat_value"],
            "roll": result["roll"],
            "rp_bonus": result["rp_bonus"],
            "modifier_total": result["modifier_total"],
            "non_rp_bonus": result["non_rp_bonus"],
            "difficulty": result["difficulty"],
            "target_resource": target_resource,
            "is_foreign": (conversion_target or {}).get("is_foreign"),
            "outcome_tier": result["outcome_tier"],
            "effect_summary": effect_summary.strip(),
        }
        attempts.append(entry)
        if effect_summary:
            log_lines.append(effect_summary)
        else:
            log_lines.append(
                f"{old_nation.get('name', 'Unknown')} attempted {mrp_def.get('name', key)} "
                f"({result['outcome_tier'].replace('_', ' ')}) — no effect.\n"
            )

        if mrp_def.get("cooldown_sessions", 0):
            pops_used_cooldowns[key] = current_session

    new_nation["mrp_cooldowns"] = pops_used_cooldowns
    return attempts, log_lines


def ai_mech_rp_tick(old_nation, new_nation, schema):
    """Nation tick: AI nations attempt up to MAX_ATTEMPTS_PER_SESSION mech RPs
    per session, chosen to serve their already-selected strategic goal (set
    earlier in the same tick pass by AI Decision Tick). No-ops for Player
    nations. Must run after "AI Decision Tick" in NATION_TICK_FUNCTIONS.
    """
    if old_nation.get("temperament", "Player") == "Player":
        return ""

    try:
        ai_state = new_nation.get("ai_state") or old_nation.get("ai_state") or {}
        goal = ai_state.get("strategic_goal") or {}
        secondary_goal = ai_state.get("secondary_goal")

        personality = get_ai_personality(old_nation)
        state = evaluate_nation_state(old_nation)
        market_prices = get_stored_market_prices(old_nation)

        from app_core import mongo as _mongo
        global_modifiers = _mongo.db["global_modifiers"].find_one({"name": "global_modifiers"})
        state["_current_session"] = global_modifiers.get("session_counter", 0) if global_modifiers else 0

        if not goal:
            # AI Decision Tick hasn't run yet this session (or errored) —
            # fall back to computing a goal fresh rather than skipping
            # mech RPs entirely.
            goal, _candidates = select_strategic_goal(old_nation, state, personality, 0.0, market_prices)

        attempts, log_lines = select_mech_rps(
            old_nation, new_nation, state, goal, secondary_goal, personality, schema, market_prices
        )

        new_ai_state = dict(ai_state)
        new_ai_state["mech_rps"] = attempts
        new_nation["ai_state"] = new_ai_state

        return "".join(log_lines)
    except Exception as e:
        import traceback
        name = old_nation.get("name", "Unknown")
        return f"{name}: Mech RP tick error: {e}\n{traceback.format_exc()}\n"
