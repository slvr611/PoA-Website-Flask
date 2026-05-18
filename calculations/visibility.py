"""
Visibility system — computes what tier (0-4) of information a viewer nation
can see about a target nation.

Tier 0: Basic public info (name, region, pop count, ruler name)
Tier 1: Demographics, pacts, ruler stats
Tier 2: Stability, laws, income
Tier 3: Districts, wonders, modifiers
Tier 4: Military and current resources (full info)
"""

from bson import ObjectId
from app_core import mongo, json_data


def _is_visibility_modifier(modifier: dict) -> bool:
    mod_type = modifier.get("modifier_type", "")
    if not mod_type:
        return False
    type_def = json_data.get("modifier_types", {}).get(mod_type, {})
    return bool(type_def.get("is_visibility_modifier", False))


def collect_visibility_modifiers(nation: dict) -> list:
    """
    Collect all offensive/defensive visibility modifiers from a nation's
    direct modifiers array and its district definitions.
    Returns a structured list for use in compute_visibility.
    """
    from calculations.field_calculations import _resolve_def

    result = []

    def _extract(m: dict, source_label: str = ""):
        mod_type = m.get("modifier_type", "")
        value = m.get("value", 0)
        src = m.get("source", source_label)
        if mod_type == "offensive_visibility":
            result.append({
                "type": "offensive",
                "value": value,
                "target_type": m.get("target_type", "all_nations"),
                "target_value": m.get("target_value") or "",
                "source": src,
            })
        elif mod_type == "defensive_visibility":
            result.append({
                "type": "defensive",
                "value": value,
                "source": src,
            })

    for m in nation.get("modifiers", []):
        _extract(m)

    for district in nation.get("districts", []):
        if not isinstance(district, dict) or not district.get("def_key"):
            continue
        dd = _resolve_def(district)
        if not dd:
            continue
        label = dd.get("display_name", district["def_key"])
        for m in dd.get("modifiers", []):
            _extract(m, label)

    return result


def get_viewer_nation(g_user) -> dict | None:
    """
    Return the minimal nation document for the logged-in user's ruling nation,
    or None if they have no ruling nation.
    """
    if not g_user:
        return None
    player = mongo.db.players.find_one({"id": g_user.get("id")}, {"_id": 1})
    if not player:
        return None
    character = mongo.db.characters.find_one(
        {"player": str(player["_id"]), "ruling_nation_org": {"$exists": True, "$ne": None}},
        {"ruling_nation_org": 1}
    )
    if not character or not character.get("ruling_nation_org"):
        return None
    try:
        nation_id = ObjectId(str(character["ruling_nation_org"]))
    except Exception:
        return None
    return mongo.db.nations.find_one(
        {"_id": nation_id},
        {"_id": 1, "region": 1, "overlord": 1, "name": 1, "visibility_modifiers": 1}
    )


def compute_visibility(viewer_nation: dict, target_nation_id: str) -> int:
    """
    Compute the visibility tier (0-4) that viewer_nation has into target_nation_id.
    """
    viewer_id = str(viewer_nation["_id"])
    if viewer_id == target_nation_id:
        return 4

    try:
        target_oid = ObjectId(target_nation_id)
    except Exception:
        return 0

    target = mongo.db.nations.find_one(
        {"_id": target_oid},
        {"_id": 1, "region": 1, "overlord": 1, "name": 1, "visibility_modifiers": 1}
    )
    if not target:
        return 0

    bonus = 0

    # Same region
    viewer_region = str(viewer_nation.get("region") or "")
    target_region = str(target.get("region") or "")
    if viewer_region and viewer_region == target_region:
        bonus += 1

    # Vassal / Overlord (either direction)
    if str(target.get("overlord") or "") == viewer_id:
        bonus += 1
    elif str(viewer_nation.get("overlord") or "") == target_nation_id:
        bonus += 1

    # Shared market
    viewer_markets = {
        str(ml["market"])
        for ml in mongo.db.market_links.find({"member": viewer_id}, {"market": 1})
        if ml.get("market")
    }
    if viewer_markets:
        target_markets = {
            str(ml["market"])
            for ml in mongo.db.market_links.find({"member": target_nation_id}, {"market": 1})
            if ml.get("market")
        }
        if viewer_markets & target_markets:
            bonus += 1

    # Diplomatic pact
    pact = mongo.db.diplo_relations.find_one(
        {
            "$or": [
                {"nation_1": viewer_id, "nation_2": target_nation_id},
                {"nation_1": target_nation_id, "nation_2": viewer_id},
            ],
            "pact_type": {"$in": ["Non-Aggression Pact", "Defensive Pact", "Military Alliance"]},
        },
        {"pact_type": 1}
    )
    if pact:
        pact_type = pact.get("pact_type", "")
        if pact_type == "Non-Aggression Pact":
            bonus += 1
        elif pact_type in ("Defensive Pact", "Military Alliance"):
            bonus += 2

    # Offensive visibility modifiers from viewer
    offensive_bonus = 0
    needs_target_region = any(
        vm.get("target_type") == "region"
        for vm in viewer_nation.get("visibility_modifiers", [])
        if vm.get("type") == "offensive"
    )
    target_region_name = ""
    if needs_target_region and target_region:
        try:
            region_doc = mongo.db.regions.find_one({"_id": ObjectId(target_region)}, {"name": 1})
            target_region_name = region_doc.get("name", "") if region_doc else ""
        except Exception:
            pass

    for vm in viewer_nation.get("visibility_modifiers", []):
        if vm.get("type") != "offensive":
            continue
        tt = vm.get("target_type", "all_nations")
        tv = vm.get("target_value", "")
        if tt == "all_nations":
            offensive_bonus += vm.get("value", 0)
        elif tt == "region" and tv and tv == target_region_name:
            offensive_bonus += vm.get("value", 0)
        elif tt == "specific_nation" and tv and tv == target.get("name", ""):
            offensive_bonus += vm.get("value", 0)

    # Defensive visibility modifiers on target
    defensive_penalty = sum(
        vm.get("value", 0)
        for vm in target.get("visibility_modifiers", [])
        if vm.get("type") == "defensive"
    )

    return max(0, min(4, bonus + offensive_bonus + defensive_penalty))
