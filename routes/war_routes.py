from flask import Blueprint, render_template, request, redirect, flash, jsonify, abort
from app_core import mongo, category_data
from helpers.auth_helpers import admin_required
from bson import ObjectId
from pymongo import ASCENDING

war_routes = Blueprint("war_routes", __name__)

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
    }
    return payload, None


# ---------------------------------------------------------------------------
# War creation
# ---------------------------------------------------------------------------

@war_routes.route("/wars/create", methods=["GET"])
@admin_required
def create_war_form():
    nations = list(mongo.db.nations.find({}, {"_id": 1, "name": 1}).sort("name", ASCENDING))
    return render_template("war_create.html", nations=nations, current_session=_current_session())


@war_routes.route("/wars/create", methods=["POST"])
@admin_required
def create_war():
    name = request.form.get("name", "").strip()
    attacker_ids = request.form.getlist("attackers")
    defender_ids = request.form.getlist("defenders")

    if not name:
        flash("War name is required.")
        return redirect("/wars/create")
    if not attacker_ids and not defender_ids:
        flash("At least one participant is required.")
        return redirect("/wars/create")

    war_doc = {"name": name, "session_declared": _current_session()}
    war_id = mongo.db.wars.insert_one(war_doc).inserted_id

    for nation_id in attacker_ids:
        mongo.db.war_links.insert_one({
            "war": str(war_id),
            "participant": nation_id,
            "stance": "Attacker",
        })
    for nation_id in defender_ids:
        mongo.db.war_links.insert_one({
            "war": str(war_id),
            "participant": nation_id,
            "stance": "Defender",
        })

    flash(f"War '{name}' created.")
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

@war_routes.route("/wars/item/<item_ref>")
def war_item(item_ref):
    war, war_id_str = _fetch_war(item_ref)

    # Participants from war_links
    links = list(mongo.db.war_links.find({"war": war_id_str}))

    attackers = []
    defenders = []
    unassigned = []

    for link in links:
        participant_id = link.get("participant", "")
        stance = link.get("stance", "")

        try:
            nation = mongo.db.nations.find_one({"_id": ObjectId(participant_id)})
        except Exception:
            nation = None
        if not nation:
            continue

        nation_id_str = str(nation["_id"])

        rulers = list(
            mongo.db.characters.find(
                {"ruling_nation_org": nation_id_str},
                {"name": 1, "strategy": 1, "character_type": 1},
            ).sort("name", ASCENDING)
        )

        unit_types = _unit_types_for_nation(nation, nation_id_str)

        entry = {
            "nation": nation,
            "nation_id": nation_id_str,
            "rulers": rulers,
            "unit_types": unit_types,
            "link": f"/nations/item/{nation.get('name', nation_id_str)}",
        }

        if stance == "Attacker":
            attackers.append(entry)
        elif stance == "Defender":
            defenders.append(entry)
        else:
            unassigned.append(entry)

    return render_template(
        "war_item.html",
        war=war,
        war_id=war_id_str,
        attackers=attackers,
        defenders=defenders,
        unassigned=unassigned,
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
