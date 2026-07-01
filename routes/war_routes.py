from flask import Blueprint, render_template, request, redirect, flash, jsonify, abort
from app_core import mongo, category_data, json_data
from helpers.auth_helpers import admin_required
from helpers.hex_map_helpers import get_all_tiles
from bson import ObjectId
from pymongo import ASCENDING

war_routes = Blueprint("war_routes", __name__)

# ---------------------------------------------------------------------------
# War type helpers
# ---------------------------------------------------------------------------

def _war_types():
    return json_data.get("war_types", {})


def _get_infamy_for_war(war_type_key, aggressor_religion_name=None, defender_religion_name=None):
    """Return infamy cost for a war type, applying Holy War religion modifiers."""
    wt = _war_types().get(war_type_key, {})
    infamy = wt.get("infamy", 0)
    if war_type_key == "holy_war" and aggressor_religion_name and defender_religion_name:
        rel_type = _religion_relationship(aggressor_religion_name, defender_religion_name)
        if rel_type == "alien":
            infamy = max(0, infamy - 5)
        elif rel_type == "kindred":
            infamy += 5
    return infamy


def _religion_relationship(rel_a, rel_b):
    """Return 'alien', 'kindred', or 'neutral' based on religion types."""
    if not rel_a or not rel_b or rel_a == rel_b:
        return "neutral"
    rels = list(mongo.db.religions.find({"name": {"$in": [rel_a, rel_b]}}, {"name": 1, "religion_type": 1}))
    types = {r["name"]: r.get("religion_type", "") for r in rels}
    type_a, type_b = types.get(rel_a, ""), types.get(rel_b, "")

    ALIEN_PAIRS = {frozenset(["Monotheistic", "Pantheistic"]), frozenset(["Monotheistic", "Animistic"])}
    KINDRED_PAIRS = {frozenset(["Pantheistic", "Animistic"])}
    pair = frozenset([type_a, type_b])
    if pair in ALIEN_PAIRS:
        return "alien"
    if pair in KINDRED_PAIRS:
        return "kindred"
    return "neutral"


# ---------------------------------------------------------------------------
# Ally / vassal resolution
# ---------------------------------------------------------------------------

def _get_nation_by_id(nation_id_str):
    try:
        return mongo.db.nations.find_one({"_id": ObjectId(nation_id_str)}, {"_id": 1, "name": 1, "overlord": 1, "primary_religion": 1, "vassal_type": 1})
    except Exception:
        return None


def _get_allies(nation_id_str, defensive_only=False):
    """Return list of nation_id_str for nations allied to nation_id_str.

    defensive_only=True: only Defensive Pact partners.
    defensive_only=False: Military Alliance + Defensive Pact partners.
    """
    pact_types = ["Defensive Pact"] if defensive_only else ["Military Alliance", "Defensive Pact"]
    query = {
        "$or": [
            {"nation_1": nation_id_str},
            {"nation_2": nation_id_str},
        ],
        "pact_type": {"$in": pact_types},
    }
    rels = list(mongo.db.diplo_relations.find(query, {"nation_1": 1, "nation_2": 1}))
    allies = []
    for rel in rels:
        partner = rel["nation_2"] if rel["nation_1"] == nation_id_str else rel["nation_1"]
        allies.append(partner)
    return allies


def _get_direct_vassals(nation_id_str):
    """Return list of nation_id_str for direct vassals of a nation."""
    vassals = list(mongo.db.nations.find(
        {"overlord": nation_id_str},
        {"_id": 1}
    ))
    return [str(v["_id"]) for v in vassals]


def _get_all_vassals_recursive(nation_id_str, visited=None):
    """Recursively gather all vassal nation IDs (vassals of vassals, etc.)."""
    if visited is None:
        visited = set()
    if nation_id_str in visited:
        return []
    visited.add(nation_id_str)
    result = []
    for vassal_id in _get_direct_vassals(nation_id_str):
        if vassal_id not in visited:
            result.append(vassal_id)
            result.extend(_get_all_vassals_recursive(vassal_id, visited))
    return result


def _resolve_war_participants(
    primary_aggressor_id,
    primary_defender_id,
    war_type_key,
    called_ally_ids=None,
):
    """Resolve all war participants with their roles.

    Returns list of dicts:
        {"nation_id": str, "stance": "Attacker"|"Defender", "role": "primary"|"ally"|"vassal"}
    """
    wt = _war_types().get(war_type_key, {})
    no_allies = wt.get("no_allies", False)
    no_vassals = wt.get("no_vassals", False)

    if called_ally_ids is None:
        called_ally_ids = []

    participants = []
    seen = set()

    def _add(nation_id, stance, role):
        if nation_id and nation_id not in seen:
            seen.add(nation_id)
            participants.append({"nation_id": nation_id, "stance": stance, "role": role})

    # Primary participants
    _add(primary_aggressor_id, "Attacker", "primary")
    _add(primary_defender_id, "Defender", "primary")

    if not no_allies:
        # Attacker: explicitly called allies (Military Alliance / Defensive Pact — attacker chooses)
        for ally_id in called_ally_ids:
            if ally_id not in seen:
                _add(ally_id, "Attacker", "ally")
                # Allies bring their vassals
                if not no_vassals:
                    for vassal_id in _get_all_vassals_recursive(ally_id):
                        _add(vassal_id, "Attacker", "vassal")

        # Defender: all their allies join automatically
        # Military Alliance partners always join; Defensive Pact partners join all non-raid wars
        defender_ally_types = ["Military Alliance", "Defensive Pact"]
        for ally_id in _get_allies(primary_defender_id, defensive_only=False):
            if ally_id not in seen:
                _add(ally_id, "Defender", "ally")
                # Allies bring their vassals
                if not no_vassals:
                    for vassal_id in _get_all_vassals_recursive(ally_id):
                        _add(vassal_id, "Defender", "vassal")

    # Vassal chains for the primary participants themselves
    if not no_vassals:
        for vassal_id in _get_all_vassals_recursive(primary_aggressor_id):
            _add(vassal_id, "Attacker", "vassal")
        for vassal_id in _get_all_vassals_recursive(primary_defender_id):
            _add(vassal_id, "Defender", "vassal")

    return participants

_S3_MAPS_BASE = "https://poa-website-static-assets.s3.us-east-1.amazonaws.com/maps/"


def _current_session():
    gm = mongo.db.global_modifiers.find_one({"name": "global_modifiers"})
    return int((gm or {}).get("session_counter", 1))


def _current_terrain_map_url():
    """Return the S3 URL of the most recent terrain map, matching map.html logic."""
    session_num = _current_session()

    if session_num <= 17:
        filename = "PoA_Geographical_Map_Tribal_Session_1.dzi"
    elif session_num <= 42:
        filename = "PoA_Terrain_Map_Ancient_Session_18.dzi"
    elif session_num <= 53:
        filename = "PoA_Terrain_Map_Classical_Session_43.dzi"
    elif session_num == 54:
        filename = "PoA_Terrain_Map_Classical_Session_54.dzi"
    elif session_num == 55:
        filename = "PoA_Terrain_Map_Classical_Session_55.dzi"
    elif session_num <= 63:
        filename = "PoA_Terrain_Map_Classical_Session_56.dzi"
    elif session_num <= 72:
        filename = "PoA_Terrain_Map_Classical_Session_64.dzi"
    else:
        filename = "PoA_Terrain_Map_Classical_Session_73.dzi"

    return _S3_MAPS_BASE + filename


_STAT_TARGET_MAP = {
    "attack": "Attack",
    "defense": "Defense",
    "hp": "Hp",
    "damage": "Damage",
    "armor": "Armor",
    "range": "Range",
}


def _make_absolute_url(url):
    """Convert a root-relative /api/... URL to an absolute URL for external consumers."""
    if not url:
        return ""
    if url.startswith("/"):
        return request.host_url.rstrip("/") + url
    return url


_COMBAT_MOD_BOOL_KEYS = {"unit_armor_cannot_be_ignored", "leader_unit_morale_immune"}

# Maps output key → nation document field name, grouped by the unit category they apply to.
_COMBAT_MOD_GROUPS = {
    "universal": {
        "attack":               "unit_attack",
        "defense":              "unit_defense",
        "hp":                   "unit_hp",
        "damage":               "unit_damage",
        "morale":               "unit_morale",
        "speed":                "unit_speed",
        "armor":                "nation_unit_armor",
        "strength":             "unit_strength",
        "retaliationDamage":    "unit_retaliation_damage",
        "armorCannotBeIgnored": "unit_armor_cannot_be_ignored",
        "speedFirstTurn":       "unit_speed_first_turn",
        "damageVsMercenaries":  "unit_damage_against_mercenaries",
    },
    "land": {
        "attack":               "land_attack",
        "defense":              "land_defense",
        "speed":                "land_unit_speed",
        "hpMult":               "land_unit_hp_mult",
        "attackVsDamagedUnits": "land_attack_vs_damaged_units",
        "rangeWhenStationary":  "land_unit_range_when_stationary",
    },
    "naval": {
        "attack":  "naval_attack",
        "defense": "naval_defense",
        "speed":   "nation_naval_unit_speed",
    },
    "mundane": {
        "attack":  "mundane_unit_attack",
        "defense": "mundane_unit_defense",
    },
    "melee": {
        "attack":            "land_melee_unit_attack",
        "defense":           "land_melee_unit_defense",
        "retaliationDamage": "land_melee_unit_retaliation_damage",
    },
    "cavalry": {
        "speed": "land_cavalry_unit_speed",
    },
    "nonRuler": {
        "speed": "land_non_ruler_unit_speed",
    },
    "rulerUnit": {
        "attack":        "nation_ruler_unit_attack",
        "defense":       "nation_ruler_unit_defense",
        "morale":        "nation_ruler_unit_morale",
        "minimumMorale": "nation_ruler_unit_minimum_morale",
    },
    "leaderUnit": {
        "attack":       "leader_unit_attack",
        "defense":      "leader_unit_defense",
        "hp":           "leader_unit_hp",
        "damage":       "leader_unit_damage",
        "morale":       "leader_unit_morale",
        "moraleImmune": "leader_unit_morale_immune",
    },
    "mercenaryLand": {
        "attack":  "mercenary_land_attack",
        "defense": "mercenary_land_defense",
    },
    "mercenaryNaval": {
        "attack":  "mercenary_naval_attack",
        "defense": "mercenary_naval_defense",
    },
}


def _nation_combat_modifiers(nation):
    result = {}
    for group, fields in _COMBAT_MOD_GROUPS.items():
        group_data = {}
        for out_key, nation_key in fields.items():
            raw = nation.get(nation_key)
            if nation_key in _COMBAT_MOD_BOOL_KEYS:
                group_data[out_key] = bool(raw)
            elif nation_key == "land_unit_hp_mult":
                group_data[out_key] = float(raw) if raw is not None else 0.0
            else:
                group_data[out_key] = int(raw) if raw else 0
        result[group] = group_data
    return result


def _stat_breakdown_to_modifiers(stat_breakdown):
    modifiers = []
    for stat, entries in (stat_breakdown or {}).items():
        target = _STAT_TARGET_MAP.get(stat)
        if not target:
            continue
        for entry in entries:
            val = entry.get("value", 0)
            if val:
                modifiers.append({
                    "statTarget": target,
                    "modifierType": "Flat",
                    "value": int(val),
                    "source": entry.get("label", ""),
                })
    return modifiers


def _unit_types_for_nation(nation, nation_id_str):
    """Build UnitTypeDto list for a nation (own units + patron mercenaries)."""
    unit_types = []
    seen = set()  # (nation_id_str, source_id) dedup

    for prefix, assigned_field, details_field in [
        ("land", "land_units", "land_unit_details"),
        ("naval", "naval_units", "naval_unit_details"),
        ("support", "support_units", "support_unit_details"),
    ]:
        assigned = nation.get(assigned_field) or {}
        details_map = nation.get(details_field) or {}

        for unit_name, count in assigned.items():
            if not count:
                continue

            details = details_map.get(unit_name, {})
            base_name = details.get("base_name", unit_name)
            era = details.get("era", "")

            # Look up MongoDB _id for this unit type
            unit_query = {"name": base_name}
            if era:
                unit_query["era"] = era
            unit_doc = mongo.db.units.find_one(unit_query, {
                "_id": 1, "image": 1,
                "melee": 1, "ranged": 1, "cavalry": 1, "support": 1,
            })
            source_id = str(unit_doc["_id"]) if unit_doc else ""

            key = (nation_id_str, source_id or unit_name)
            if key in seen:
                continue
            seen.add(key)

            base_stats = details.get("base_stats", {})
            stat_breakdown = details.get("stat_breakdown", {})

            unit_types.append({
                "nationId":  nation_id_str,
                "name":      details.get("display_name", unit_name),
                "sourceId":  source_id,
                "iconUrl":   _make_absolute_url(unit_doc.get("image") or "") if unit_doc else "",
                "isMagical": bool(details.get("is_magical", False)),
                "isMelee":   bool(unit_doc.get("melee",   False)) if unit_doc else False,
                "isRanged":  bool(unit_doc.get("ranged",  False)) if unit_doc else False,
                "isCavalry": bool(unit_doc.get("cavalry", False)) if unit_doc else False,
                "isSupport": bool(unit_doc.get("support", False)) if unit_doc else False,
                "baseAttack":  int(base_stats.get("attack")  or 0),
                "baseDefense": int(base_stats.get("defense") or 0),
                "baseHp":      int(base_stats.get("hp")      or 0),
                "baseDamage":  int(base_stats.get("damage")  or 0),
                "baseArmor":   int(base_stats.get("armor")   or 0),
                "baseRange":   int(base_stats.get("range")   or 1),
                "count":       int(count),
                "modifiers":   _stat_breakdown_to_modifiers(stat_breakdown),
            })

    # Patron mercenaries
    mercenaries = list(mongo.db.mercenaries.find({"patron": nation_id_str}))
    for merc in mercenaries:
        for unit_field in ["land_units", "naval_units"]:
            merc_units = merc.get(unit_field) or {}
            merc_details = merc.get(unit_field.replace("_units", "_unit_details")) or {}

            for unit_name, count in merc_units.items():
                if not count:
                    continue

                details = merc_details.get(unit_name, {})
                base_name = details.get("base_name", unit_name)
                era = details.get("era", "")

                unit_query = {"name": base_name} if base_name else {"name": unit_name}
                if era:
                    unit_query["era"] = era
                unit_doc = mongo.db.units.find_one(unit_query, {
                    "_id": 1, "image": 1,
                    "melee": 1, "ranged": 1, "cavalry": 1, "support": 1,
                    "attack": 1, "defense": 1, "hp": 1, "damage": 1,
                    "armor": 1, "maximum_range": 1, "has_attack": 1, "has_defense": 1,
                    "has_hp": 1, "has_damage": 1, "has_armor": 1, "has_range": 1,
                })
                if not unit_doc:
                    continue
                source_id = str(unit_doc["_id"])

                key = (nation_id_str, source_id)
                if key in seen:
                    # Merge count into existing entry
                    for ut in unit_types:
                        if ut["nationId"] == nation_id_str and ut["sourceId"] == source_id:
                            ut["count"] = ut.get("count", 0) + int(count)
                    continue
                seen.add(key)

                if details.get("base_stats"):
                    base_stats = details["base_stats"]
                    stat_breakdown = details.get("stat_breakdown", {})
                    base_attack = int(base_stats.get("attack") or 0)
                    base_defense = int(base_stats.get("defense") or 0)
                    base_hp = int(base_stats.get("hp") or 0)
                    base_damage = int(base_stats.get("damage") or 0)
                    base_armor = int(base_stats.get("armor") or 0)
                    base_range = int(base_stats.get("range") or 1)
                    modifiers = _stat_breakdown_to_modifiers(stat_breakdown)
                else:
                    base_attack = int(unit_doc.get("attack") or 0) if unit_doc.get("has_attack", True) else 0
                    base_defense = int(unit_doc.get("defense") or 0) if unit_doc.get("has_defense", True) else 0
                    base_hp = int(unit_doc.get("hp") or 0) if unit_doc.get("has_hp") else 0
                    base_damage = int(unit_doc.get("damage") or 0) if unit_doc.get("has_damage") else 0
                    base_armor = int(unit_doc.get("armor") or 0) if unit_doc.get("has_armor") else 0
                    base_range = int(unit_doc.get("maximum_range") or 1) if unit_doc.get("has_range") else 1
                    modifiers = []

                unit_types.append({
                    "nationId":  nation_id_str,
                    "name":      details.get("display_name", unit_name) or unit_name,
                    "sourceId":  source_id,
                    "iconUrl":   _make_absolute_url(unit_doc.get("image") or ""),
                    "isMagical": bool(details.get("is_magical", False)),
                    "isMelee":   bool(unit_doc.get("melee",   False)),
                    "isRanged":  bool(unit_doc.get("ranged",  False)),
                    "isCavalry": bool(unit_doc.get("cavalry", False)),
                    "isSupport": bool(unit_doc.get("support", False)),
                    "baseAttack":  base_attack,
                    "baseDefense": base_defense,
                    "baseHp":      base_hp,
                    "baseDamage":  base_damage,
                    "baseArmor":   base_armor,
                    "baseRange":   base_range,
                    "count":       int(count),
                    "mercenaryName": merc.get("name", ""),
                    "modifiers":   modifiers,
                })

    return unit_types


def _build_hex_map_data():
    """Collect the current world map config and all tile data for the war payload."""
    cfg = mongo.db.global_modifiers.find_one({"name": "hex_map_config"}) or {}
    tiles_raw = get_all_tiles()

    tiles = []
    for t in tiles_raw:
        entry = {"q": t.get("q"), "r": t.get("r")}
        for field in ("owner", "terrain", "region", "capital", "portal"):
            val = t.get(field)
            if val is not None and val != "" and val is not False:
                entry[field] = val
        for obj_field in ("city", "district", "wonder"):
            obj = t.get(obj_field)
            if obj and isinstance(obj, dict) and obj.get("id"):
                entry[obj_field] = {"id": obj["id"], "name": obj.get("name", ""), "type": obj.get("type", "")}
        tiles.append(entry)

    return {
        "cols":      cfg.get("cols",        20),
        "rows":      cfg.get("rows",        15),
        "hexSize":   cfg.get("hex_size",    40),
        "bgOffsetX": cfg.get("bg_offset_x",  0),
        "bgOffsetY": cfg.get("bg_offset_y",  0),
        "bgScale":   cfg.get("bg_scale",    1.0),
        "tiles":     tiles,
    }


def _build_war_payload(war_id_strings):
    """Build the WarPayload dict for one or more war IDs."""
    wars_dto = []
    nations_dto = []
    rulers_dto = []
    unit_types_dto = []

    for war_idx, war_id_str in enumerate(war_id_strings):
        try:
            war = mongo.db.wars.find_one({"_id": ObjectId(war_id_str)})
        except Exception:
            return None, f"Invalid war_id: {war_id_str}"
        if not war:
            return None, f"War not found: {war_id_str}"

        wars_dto.append({"name": war.get("name", ""), "sourceWarId": war_id_str})

        links = list(mongo.db.war_links.find({"war": war_id_str}))
        for link in links:
            participant_id = link.get("participant", "")
            stance = link.get("stance", "Attacker")

            try:
                nation = mongo.db.nations.find_one({"_id": ObjectId(participant_id)})
            except Exception:
                continue
            if not nation:
                continue

            nation_id_str = str(nation["_id"])

            # Resolve SpacetimeDB identity from the ruling player
            controller_identity = ""
            ruler_char = mongo.db.characters.find_one(
                {"ruling_nation_org": nation_id_str}, {"player": 1}
            )
            if ruler_char and ruler_char.get("player"):
                try:
                    player = mongo.db.players.find_one(
                        {"_id": ObjectId(ruler_char["player"])},
                        {"spacetimedb_identity": 1},
                    )
                    if player:
                        controller_identity = player.get("spacetimedb_identity", "")
                except Exception:
                    pass

            nations_dto.append({
                "nationId":          nation_id_str,
                "name":              nation.get("name", ""),
                "accentColor":       nation.get("accent_color", "#4a90d9"),
                "flagUrl":           _make_absolute_url(nation.get("flag_url") or ""),
                "warIdIndex":        war_idx,
                "stance":            stance,
                "controllerIdentity": controller_identity or "",
                "combatModifiers":   _nation_combat_modifiers(nation),
            })

            # Rulers – all characters whose ruling_nation_org is this nation
            rulers = list(
                mongo.db.characters.find(
                    {"ruling_nation_org": nation_id_str},
                    {"name": 1, "strategy": 1},
                )
            )
            for ruler in rulers:
                rulers_dto.append({
                    "nationId": nation_id_str,
                    "name": ruler.get("name", ""),
                    "initiative": int(ruler.get("strategy") or 0),
                    "sourceId": str(ruler["_id"]),
                })

            unit_types_dto.extend(_unit_types_for_nation(nation, nation_id_str))

    session_name = (
        wars_dto[0]["name"] if len(wars_dto) == 1
        else " & ".join(w["name"] for w in wars_dto)
    )

    payload = {
        "name": session_name,
        "terrainImageUrl": _current_terrain_map_url(),
        "wars": wars_dto,
        "nations": nations_dto,
        "rulers": rulers_dto,
        "unitTypes": unit_types_dto,
        "hexMap": _build_hex_map_data(),
    }
    return payload, None


# ---------------------------------------------------------------------------
# War creation
# ---------------------------------------------------------------------------

@war_routes.route("/wars/create", methods=["GET"])
@admin_required
def create_war_form():
    nations = list(mongo.db.nations.find(
        {}, {"_id": 1, "name": 1, "primary_religion": 1}
    ).sort("name", ASCENDING))
    war_types = _war_types()

    # For each nation build its list of potential allies (military alliance + defensive pact partners)
    nation_allies = {}
    for n in nations:
        nid = str(n["_id"])
        allies = _get_allies(nid, defensive_only=False)
        if allies:
            nation_allies[nid] = allies

    return render_template(
        "war_create.html",
        nations=nations,
        war_types=war_types,
        nation_allies=nation_allies,
        current_session=_current_session(),
    )


@war_routes.route("/wars/create", methods=["POST"])
@admin_required
def create_war():
    name = request.form.get("name", "").strip()
    war_type_key = request.form.get("war_type", "").strip()
    primary_aggressor_id = request.form.get("primary_aggressor", "").strip()
    primary_defender_id = request.form.get("primary_defender", "").strip()
    called_ally_ids = request.form.getlist("called_allies")

    if not name:
        flash("War name is required.")
        return redirect("/wars/create")
    if not primary_aggressor_id or not primary_defender_id:
        flash("Both a primary aggressor and primary defender are required.")
        return redirect("/wars/create")
    if primary_aggressor_id == primary_defender_id:
        flash("Primary aggressor and primary defender cannot be the same nation.")
        return redirect("/wars/create")
    if war_type_key not in _war_types():
        flash("A valid war type is required.")
        return redirect("/wars/create")

    # Resolve all participants using ally/vassal logic
    participants = _resolve_war_participants(
        primary_aggressor_id,
        primary_defender_id,
        war_type_key,
        called_ally_ids,
    )

    # Calculate and apply infamy to primary aggressor
    aggressor = _get_nation_by_id(primary_aggressor_id)
    defender = _get_nation_by_id(primary_defender_id)
    aggressor_religion = aggressor.get("primary_religion", "") if aggressor else ""
    defender_religion = defender.get("primary_religion", "") if defender else ""
    infamy_cost = _get_infamy_for_war(war_type_key, aggressor_religion, defender_religion)

    war_doc = {
        "name": name,
        "war_type": war_type_key,
        "primary_aggressor": primary_aggressor_id,
        "primary_defender": primary_defender_id,
        "session_declared": _current_session(),
    }
    war_id = mongo.db.wars.insert_one(war_doc).inserted_id
    war_id_str = str(war_id)

    for p in participants:
        mongo.db.war_links.insert_one({
            "war": war_id_str,
            "participant": p["nation_id"],
            "stance": p["stance"],
            "role": p["role"],
        })

    # Apply infamy to the primary aggressor
    if infamy_cost > 0 and aggressor:
        mongo.db.nations.update_one(
            {"_id": ObjectId(primary_aggressor_id)},
            {"$inc": {"infamy": infamy_cost}},
        )

    flash(f"War '{name}' created ({infamy_cost} infamy applied to {aggressor.get('name', 'aggressor') if aggressor else 'aggressor'}).")
    return redirect(f"/wars/item/{name}")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _fetch_war(item_ref):
    """Return (war_doc, war_id_str) or raise 404."""
    war = mongo.db.wars.find_one({"name": item_ref})
    if not war:
        try:
            war = mongo.db.wars.find_one({"_id": ObjectId(item_ref)})
        except Exception:
            pass
    if not war:
        abort(404)
    return war, str(war["_id"])


def _current_participants(war_id_str):
    """Return list of {link_id, nation, nation_id_str, stance} for a war."""
    links = list(mongo.db.war_links.find({"war": war_id_str}))
    participants = []
    for link in links:
        participant_id = link.get("participant", "")
        try:
            nation = mongo.db.nations.find_one(
                {"_id": ObjectId(participant_id)}, {"_id": 1, "name": 1}
            )
        except Exception:
            nation = None
        if not nation:
            continue
        participants.append({
            "link_id": str(link["_id"]),
            "nation": nation,
            "nation_id_str": str(nation["_id"]),
            "stance": link.get("stance", ""),
        })
    participants.sort(key=lambda p: p["nation"].get("name", ""))
    return participants


# ---------------------------------------------------------------------------
# Custom war item view (overrides the generic dataItem view for wars)
# ---------------------------------------------------------------------------

def _nation_discord_info(nation_id_str):
    """Return (discord_id, is_player) for a nation. Checks character -> player chain."""
    char = mongo.db.characters.find_one(
        {"ruling_nation_org": nation_id_str, "player": {"$exists": True, "$ne": None, "$ne": ""}},
        {"player": 1},
    )
    if not char or not char.get("player"):
        return None, False
    try:
        player = mongo.db.players.find_one({"_id": ObjectId(char["player"])}, {"id": 1})
        if player and player.get("id"):
            return player["id"], True
    except Exception:
        pass
    return None, False


@war_routes.route("/wars/item/<item_ref>")
def war_item(item_ref):
    war, war_id_str = _fetch_war(item_ref)

    war_type_key = war.get("war_type", "")
    war_type_data = _war_types().get(war_type_key, {})

    # Participants from war_links, enriched with role
    links = list(mongo.db.war_links.find({"war": war_id_str}))

    attackers = []
    defenders = []
    unassigned = []

    for link in links:
        participant_id = link.get("participant", "")
        stance = link.get("stance", "")
        role = link.get("role", "")

        try:
            nation = mongo.db.nations.find_one({"_id": ObjectId(participant_id)})
        except Exception:
            nation = None
        if not nation:
            continue

        nation_id_str = str(nation["_id"])
        is_primary = (
            nation_id_str == war.get("primary_aggressor")
            or nation_id_str == war.get("primary_defender")
            or role == "primary"
        )

        rulers = list(
            mongo.db.characters.find(
                {"ruling_nation_org": nation_id_str},
                {"name": 1, "strategy": 1, "character_type": 1},
            ).sort("name", ASCENDING)
        )

        unit_types = _unit_types_for_nation(nation, nation_id_str)

        discord_id, is_player = _nation_discord_info(nation_id_str)

        entry = {
            "nation": nation,
            "nation_id": nation_id_str,
            "rulers": rulers,
            "unit_types": unit_types,
            "link": f"/nations/item/{nation.get('name', nation_id_str)}",
            "role": role or ("primary" if is_primary else ""),
            "is_primary": is_primary,
            "discord_id": discord_id,
            "is_player": is_player,
        }

        if stance == "Attacker":
            attackers.append(entry)
        elif stance == "Defender":
            defenders.append(entry)
        else:
            unassigned.append(entry)

    # Sort: primaries first within each side, then allies, then vassals
    def _sort_key(e):
        role_order = {"primary": 0, "ally": 1, "vassal": 2, "": 3}
        return (role_order.get(e.get("role", ""), 3), e["nation"].get("name", ""))
    attackers.sort(key=_sort_key)
    defenders.sort(key=_sort_key)

    return render_template(
        "war_item.html",
        war=war,
        war_id=war_id_str,
        attackers=attackers,
        defenders=defenders,
        unassigned=unassigned,
        war_type_data=war_type_data,
        war_type_key=war_type_key,
        war_goals_data=json_data.get("war_goals", {}),
    )


# ---------------------------------------------------------------------------
# War edit
# ---------------------------------------------------------------------------

@war_routes.route("/wars/edit/<item_ref>", methods=["GET"])
@admin_required
def edit_war_form(item_ref):
    war, war_id_str = _fetch_war(item_ref)
    participants = _current_participants(war_id_str)
    all_nations = list(mongo.db.nations.find({}, {"_id": 1, "name": 1}).sort("name", ASCENDING))
    current_session = _current_session()

    return render_template(
        "war_edit.html",
        war=war,
        war_id=war_id_str,
        participants=participants,
        all_nations=all_nations,
        current_session=current_session,
        war_types=_war_types(),
        war_goals_data=json_data.get("war_goals", {}),
    )


@war_routes.route("/wars/edit/<item_ref>/save", methods=["POST"])
@admin_required
def save_war(item_ref):
    war, war_id_str = _fetch_war(item_ref)

    # --- Update war name and session fields ---
    updates = {}
    new_name = request.form.get("name", "").strip()
    if new_name and new_name != war.get("name", ""):
        updates["name"] = new_name

    raw_ended = request.form.get("session_ended", "").strip()
    if raw_ended == "" or raw_ended == "ongoing":
        updates["session_ended"] = None
    else:
        try:
            updates["session_ended"] = int(raw_ended)
        except ValueError:
            pass

    if updates:
        mongo.db.wars.update_one({"_id": war["_id"]}, {"$set": updates})

    # --- Parse submitted participant rows (participants-N-* indexed fields) ---
    submitted = []
    i = 0
    while request.form.get(f"participants-{i}-nation_id") is not None or \
          request.form.get(f"participants-{i}-link_id") is not None:
        link_id  = request.form.get(f"participants-{i}-link_id",  "").strip()
        nation_id = request.form.get(f"participants-{i}-nation_id", "").strip()
        stance   = request.form.get(f"participants-{i}-stance",   "Attacker").strip()
        if stance not in ("Attacker", "Defender"):
            stance = "Attacker"
        if nation_id:
            submitted.append({"link_id": link_id, "nation_id": nation_id, "stance": stance})
        i += 1

    # Index existing links by their _id string
    current_links = {
        str(lnk["_id"]): lnk
        for lnk in mongo.db.war_links.find({"war": war_id_str})
    }
    submitted_link_ids = set()

    for row in submitted:
        link_id   = row["link_id"]
        nation_id = row["nation_id"]
        stance    = row["stance"]

        if link_id and link_id in current_links:
            # Existing participant — update stance if changed
            submitted_link_ids.add(link_id)
            if current_links[link_id].get("stance") != stance:
                mongo.db.war_links.update_one(
                    {"_id": ObjectId(link_id)},
                    {"$set": {"stance": stance}},
                )
        else:
            # New participant — insert (guard against duplicates)
            already = mongo.db.war_links.find_one(
                {"war": war_id_str, "participant": nation_id}
            )
            if not already:
                mongo.db.war_links.insert_one({
                    "war": war_id_str,
                    "participant": nation_id,
                    "stance": stance,
                })

    # Remove any existing links that were deleted from the form
    for link_id, lnk in current_links.items():
        if link_id not in submitted_link_ids:
            mongo.db.war_links.delete_one({"_id": lnk["_id"]})

    # --- Parse submitted war goals (goals-N-* indexed fields) ---
    war_goals_list = []
    i = 0
    while request.form.get(f"goals-{i}-goal_key") is not None:
        goal_key   = request.form.get(f"goals-{i}-goal_key",  "").strip()
        slot_tier  = request.form.get(f"goals-{i}-slot_tier", "").strip()
        attacker   = request.form.get(f"goals-{i}-attacker",  "").strip()
        target     = request.form.get(f"goals-{i}-target",    "").strip()
        notes      = request.form.get(f"goals-{i}-notes",     "").strip()
        if goal_key and slot_tier:
            war_goals_list.append({
                "goal_key":  goal_key,
                "slot_tier": slot_tier,
                "attacker":  attacker,
                "target":    target,
                "notes":     notes,
            })
        i += 1
    mongo.db.wars.update_one({"_id": war["_id"]}, {"$set": {"war_goals": war_goals_list}})

    display_name = new_name or war.get("name", war_id_str)
    flash(f"War '{display_name}' updated.")
    return redirect(f"/wars/item/{display_name}")


# ---------------------------------------------------------------------------
# API – war session data
# ---------------------------------------------------------------------------

@war_routes.route("/api/war-session-data/<war_id>")
def war_session_data_by_id(war_id):
    payload, error = _build_war_payload([war_id])
    if error:
        return jsonify({"error": error}), 400 if "Invalid" in error else 404
    return jsonify(payload)


@war_routes.route("/api/war-session-data")
def war_session_data():
    raw = request.args.get("war_ids") or request.args.get("war_id") or ""
    war_id_strings = [w.strip() for w in raw.split(",") if w.strip()]
    if not war_id_strings:
        return jsonify({"error": "war_ids parameter required"}), 400

    payload, error = _build_war_payload(war_id_strings)
    if error:
        return jsonify({"error": error}), 400 if "Invalid" in error else 404
    return jsonify(payload)


@war_routes.route("/api/wars")
def api_wars_list():
    """Return a summary list of all wars with their participants and stance."""
    wars = list(mongo.db.wars.find({}, {"_id": 1, "name": 1}).sort("name", ASCENDING))
    result = []
    for war in wars:
        war_id_str = str(war["_id"])
        links = list(mongo.db.war_links.find({"war": war_id_str}))

        attackers, defenders = [], []
        for lnk in links:
            try:
                nation = mongo.db.nations.find_one(
                    {"_id": ObjectId(lnk["participant"])}, {"name": 1}
                )
            except Exception:
                nation = None
            name = nation.get("name", lnk["participant"]) if nation else lnk["participant"]
            if lnk.get("stance") == "Defender":
                defenders.append(name)
            else:
                attackers.append(name)

        result.append({
            "id": war_id_str,
            "name": war.get("name", ""),
            "attackers": sorted(attackers),
            "defenders": sorted(defenders),
            "sessionDataUrl": f"/api/war-session-data/{war_id_str}",
        })

    return jsonify(result)


@war_routes.route("/api/units")
def api_all_units():
    """Return every unit type with base stats for war-calculator unit selection."""
    units = list(mongo.db.units.find({}, {
        "_id": 1, "name": 1, "image": 1, "era": 1, "unit_type": 1, "unit_class": 1,
        "melee": 1, "ranged": 1, "cavalry": 1, "support": 1,
        "has_attack": 1,            "attack": 1,
        "has_defense": 1,           "defense": 1,
        "has_hp": 1,                "hp": 1,
        "has_morale": 1,            "morale": 1,
        "has_damage": 1,            "damage": 1,
        "has_retaliation_damage": 1, "retaliation_damage": 1,
        "has_range": 1,             "minimum_range": 1, "maximum_range": 1,
        "has_speed": 1,             "speed": 1,
        "has_armor": 1,             "armor": 1,
    }).sort("name", ASCENDING))

    result = []
    for u in units:
        result.append({
            "id":        str(u["_id"]),
            "name":      u.get("name", ""),
            "era":       u.get("era", ""),
            "unitType":  u.get("unit_type", ""),
            "unitClass": u.get("unit_class", ""),
            "isMelee":   bool(u.get("melee",   False)),
            "isRanged":  bool(u.get("ranged",  False)),
            "isCavalry": bool(u.get("cavalry", False)),
            "isSupport": bool(u.get("support", False)),
            "iconUrl":   _make_absolute_url(u.get("image") or ""),
            "attack":    int(u.get("attack",  0)) if u.get("has_attack",  True)  else None,
            "defense":   int(u.get("defense", 0)) if u.get("has_defense", True)  else None,
            "hp":        int(u.get("hp",      1)) if u.get("has_hp",      True)  else None,
            "morale":    int(u.get("morale",  1)) if u.get("has_morale",  True)  else None,
            "damage":    int(u.get("damage",  1)) if u.get("has_damage",  True)  else None,
            "retaliationDamage": int(u.get("retaliation_damage", 0)) if u.get("has_retaliation_damage", False) else None,
            "minRange":  int(u.get("minimum_range", 1)) if u.get("has_range", True)  else None,
            "maxRange":  int(u.get("maximum_range", 1)) if u.get("has_range", True)  else None,
            "speed":     int(u.get("speed",   1)) if u.get("has_speed",   True)  else None,
            "armor":     int(u.get("armor",   0)) if u.get("has_armor",   False) else None,
        })

    return jsonify(result)
