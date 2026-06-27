import uuid
import math
from bson import ObjectId
from helpers.data_helpers import get_data_on_category
from helpers.ai_decision_helpers import ai_decision_tick, ai_market_matching_tick, market_price_tick
from helpers.trade_route_helpers import run_trade_route_lifecycle, _current_session as _tr_current_session
from calculations.field_calculations import calculate_all_fields
from pymongo import ASCENDING
from helpers.change_helpers import system_request_change, system_approve_change
from helpers.archive_helpers import archive_old_changes
from app_core import mongo, json_data, upload_to_s3, character_stats
from flask import flash
from app_core import backup_mongodb_async, category_data, temperament_enum, base_temperament_odds, cultural_trait_temperament_modifiers
from copy import deepcopy
import random
import os
import datetime

def tick(form_data):
    if "run_Backup Database" in form_data:
        success, message = backup_database()
        if not success:
            return message
    player_tick_summary = ""
    full_tick_summary = ""

    global_modifiers = mongo.db["global_modifiers"].find_one({"name": "global_modifiers"})
    old_target = global_modifiers
    new_target = deepcopy(global_modifiers)
    schema = category_data["global_modifiers"]["schema"]
    if global_modifiers:
        run_key = f"run_Tick Session Number"
        if run_key in form_data:
            print("Tick Session Number")
            full_tick_summary += tick_session_number(old_target, new_target, schema)



    collect_character_data = False
    for tick_function_label, tick_function in CHARACTER_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            collect_character_data = True
            break
    
    if collect_character_data:
        character_schema, character_db = get_data_on_category("characters")
        old_characters = list(character_db.find().sort("name", ASCENDING))
        new_characters = []
        for character in old_characters:
            if character:
                character.update(calculate_all_fields(character, character_schema, "character"))
                new_characters.append(deepcopy(character))
        
        for tick_function_label, tick_function in CHARACTER_TICK_FUNCTIONS.items():
            run_key = f"run_{tick_function_label}"
            if run_key in form_data:
                print(tick_function_label)
                for i in range(len(old_characters)):
                    result = tick_function(old_characters[i], new_characters[i], character_schema)
                    if old_characters[i].get("player", None) is not None:
                        player_tick_summary += result
                    full_tick_summary += result



    collect_artifact_data = False
    for tick_function_label, tick_function in ARTIFACT_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            collect_artifact_data = True
            break
    
    if collect_artifact_data:
        artifact_schema, artifact_db = get_data_on_category("artifacts")
        old_artifacts = list(artifact_db.find().sort("name", ASCENDING))
        new_artifacts = []
        for artifact in old_artifacts:
            if artifact:
                artifact.update(calculate_all_fields(artifact, artifact_schema, "artifact"))
                new_artifacts.append(deepcopy(artifact))
        
        for tick_function_label, tick_function in ARTIFACT_TICK_FUNCTIONS.items():
            run_key = f"run_{tick_function_label}"
            if run_key in form_data:
                print(tick_function_label)
                for i in range(len(old_artifacts)):
                    result = tick_function(old_artifacts[i], new_artifacts[i], artifact_schema)
                    character = old_artifacts[i].get("owner", "None")
                    if character != "None":
                        try:
                            character = character_db.find_one({"_id": ObjectId(character)})
                            if character.get("player", "None") is not None:
                                player_tick_summary += result
                        except:
                            pass
                    full_tick_summary += result


    collect_merchant_data = False
    for tick_function_label, tick_function in MERCHANT_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            collect_merchant_data = True
            break
    
    if collect_merchant_data:
        merchant_schema, merchant_db = get_data_on_category("merchants")
        old_merchants = list(merchant_db.find().sort("name", ASCENDING))
        new_merchants = []
        for merchant in old_merchants:
            if merchant:
                merchant.update(calculate_all_fields(merchant, merchant_schema, "merchant"))
                new_merchants.append(deepcopy(merchant))
        
        for tick_function_label, tick_function in MERCHANT_TICK_FUNCTIONS.items():
            run_key = f"run_{tick_function_label}"
            if run_key in form_data:
                print(tick_function_label)
                for i in range(len(old_merchants)):
                    result = tick_function(old_merchants[i], new_merchants[i], merchant_schema)
                    leaders = old_merchants[i].get("leaders", [])
                    for leader in leaders:
                        try:
                            character = character_db.find_one({"_id": ObjectId(leader)})
                            if character.get("player", "None") is not None:
                                player_tick_summary += result
                                break
                        except:
                            pass
                    full_tick_summary += result



    collect_mercenary_data = False
    for tick_function_label, tick_function in MERCENARY_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            collect_mercenary_data = True
            break
    
    if collect_mercenary_data:
        mercenary_schema, mercenary_db = get_data_on_category("mercenaries")
        old_mercenaries = list(mercenary_db.find().sort("name", ASCENDING))
        new_mercenaries = []
        for mercenary in old_mercenaries:
            if mercenary:
                mercenary.update(calculate_all_fields(mercenary, mercenary_schema, "mercenary"))
                new_mercenaries.append(deepcopy(mercenary))
        
        for tick_function_label, tick_function in MERCENARY_TICK_FUNCTIONS.items():
            run_key = f"run_{tick_function_label}"
            if run_key in form_data:
                print(tick_function_label)
                for i in range(len(old_mercenaries)):
                    result = tick_function(old_mercenaries[i], new_mercenaries[i], mercenary_schema)
                    leaders = old_mercenaries[i].get("leaders", [])
                    for leader in leaders:
                        try:
                            character = character_db.find_one({"_id": ObjectId(leader)})
                            if character.get("player", "None") is not None:
                                player_tick_summary += result
                                break
                        except:
                            pass
                    full_tick_summary += result



    collect_faction_data = False
    for tick_function_label, tick_function in FACTION_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            collect_faction_data = True
            break
    
    if collect_faction_data:
        faction_schema, faction_db = get_data_on_category("factions")
        old_factions = list(faction_db.find().sort("name", ASCENDING))
        new_factions = []
        for faction in old_factions:
            if faction:
                faction.update(calculate_all_fields(faction, faction_schema, "faction"))
                new_factions.append(deepcopy(faction))

        for tick_function_label, tick_function in FACTION_TICK_FUNCTIONS.items():
            run_key = f"run_{tick_function_label}"
            if run_key in form_data:
                print(tick_function_label)
                for i in range(len(old_factions)):
                    result = tick_function(old_factions[i], new_factions[i], faction_schema)
                    leaders = old_factions[i].get("leaders", [])
                    for leader in leaders:
                        try:
                            character = character_db.find_one({"_id": ObjectId(leader)})
                            if character.get("player", "None") is not None:
                                player_tick_summary += result
                                break
                        except:
                            pass
                    full_tick_summary += result



    collect_market_data = False
    for tick_function_label, tick_function in MARKET_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            collect_market_data = True
            break
    
    if collect_market_data:
        market_schema, market_db = get_data_on_category("markets")
        old_markets = list(market_db.find().sort("name", ASCENDING))
        new_markets = []
        for market in old_markets:
            if market:
                market.update(calculate_all_fields(market, market_schema, "market"))
                new_markets.append(deepcopy(market))

        for tick_function_label, tick_function in MARKET_TICK_FUNCTIONS.items():
            run_key = f"run_{tick_function_label}"
            if run_key in form_data:
                print(tick_function_label)
                for i in range(len(old_markets)):
                    full_tick_summary += tick_function(old_markets[i], new_markets[i], market_schema)



    collect_nation_data = False
    for tick_function_label in list(NATION_TICK_FUNCTIONS) + list(NATION_CROSS_TICK_FUNCTIONS):
        if f"run_{tick_function_label}" in form_data:
            collect_nation_data = True
            break
    
    if collect_nation_data:
        nation_schema, nation_db = get_data_on_category("nations")
        old_nations = list(nation_db.find().sort("name", ASCENDING))
        new_nations = []
        for nation in old_nations:
            if nation:
                nation.update(calculate_all_fields(nation, nation_schema, "nation"))
                new_nations.append(deepcopy(nation))
        
        for tick_function_label, tick_function in NATION_TICK_FUNCTIONS.items():
            run_key = f"run_{tick_function_label}"
            if run_key in form_data:
                print(tick_function_label)
                for i in range(len(old_nations)):
                    result = tick_function(old_nations[i], new_nations[i], nation_schema)
                    if old_nations[i].get("temperament", "None") == "Player":
                        player_tick_summary += result
                    elif tick_function_label in VASSAL_SPECIFIC_NATION_TICK_FUNCTIONS and old_nations[i].get("overlord", "None") != "None":
                        overlord = old_nations[i].get("overlord", "None")
                        try:
                            overlord = nation_db.find_one({"_id": ObjectId(overlord)})
                            if overlord.get("temperament", "None") == "Player":
                                player_tick_summary += result
                        except:
                            pass
                    full_tick_summary += result

        for tick_function_label, tick_function in NATION_CROSS_TICK_FUNCTIONS.items():
            if f"run_{tick_function_label}" in form_data:
                print(tick_function_label)
                result = tick_function(old_nations, new_nations, nation_schema)
                full_tick_summary += result



    if "run_Tick Session Number" in form_data:
        change_id = system_request_change(
            data_type="global_modifiers",
            item_id=old_target["_id"],
            change_type="Update",
            before_data=old_target,
            after_data=new_target,
            reason="Tick Update for Tick Session Number"
        )
        system_approve_change(change_id)
    
    if collect_character_data:
        for i in range(len(old_characters)):
            change_id = system_request_change(
                data_type="characters",
                item_id=old_characters[i]["_id"],
                change_type="Update",
                before_data=old_characters[i],
                after_data=new_characters[i],
                reason="Tick Update for " + old_characters[i]["name"]
            )
            system_approve_change(change_id)
    
    if collect_artifact_data:    
        for i in range(len(old_artifacts)):
            change_id = system_request_change(
                data_type="artifacts",
                item_id=old_artifacts[i]["_id"],
                change_type="Update",
                before_data=old_artifacts[i],
                after_data=new_artifacts[i],
                reason="Tick Update for " + old_artifacts[i]["name"]
            )
            system_approve_change(change_id)

    if collect_merchant_data:
        for i in range(len(old_merchants)):
            change_id = system_request_change(
                data_type="merchants",
                item_id=old_merchants[i]["_id"],
                change_type="Update",
                before_data=old_merchants[i],
                after_data=new_merchants[i],
                reason="Tick Update for " + old_merchants[i]["name"]
            )
            system_approve_change(change_id)

    if collect_mercenary_data:
        for i in range(len(old_mercenaries)):
            change_id = system_request_change(
                data_type="mercenaries",
                item_id=old_mercenaries[i]["_id"],
                change_type="Update",
                before_data=old_mercenaries[i],
                after_data=new_mercenaries[i],
                reason="Tick Update for " + old_mercenaries[i]["name"]
            )
            system_approve_change(change_id)
    
    if collect_faction_data:
        for i in range(len(old_factions)):
            change_id = system_request_change(
                data_type="factions",
                item_id=old_factions[i]["_id"],
                change_type="Update",
                before_data=old_factions[i],
                after_data=new_factions[i],
                reason="Tick Update for " + old_factions[i]["name"]
            )
            system_approve_change(change_id)

    if collect_market_data:
        for i in range(len(old_markets)):
            change_id = system_request_change(
                data_type="markets",
                item_id=old_markets[i]["_id"],
                change_type="Update",
                before_data=old_markets[i],
                after_data=new_markets[i],
                reason="Tick Update for " + old_markets[i]["name"]
            )
            system_approve_change(change_id)

    if collect_nation_data:
        for i in range(len(old_nations)):
            change_id = system_request_change(
                data_type="nations",
                item_id=old_nations[i]["_id"],
                change_type="Update",
                before_data=old_nations[i],
                after_data=new_nations[i],
                reason="Tick Update for " + old_nations[i]["name"]
            )
            system_approve_change(change_id)

    global_modifiers_refreshed = mongo.db["global_modifiers"].find_one({"name": "global_modifiers"})
    current_session = global_modifiers_refreshed.get("session_counter", 0) if global_modifiers_refreshed else 0
    archive_message = archive_old_changes(current_session)
    full_tick_summary += f"\n\nArchival: {archive_message}"

    if "run_Snapshot Hex Map" in form_data:
        from helpers.hex_map_helpers import snapshot_current_map
        snap_message = snapshot_current_map(current_session)
        full_tick_summary += f"\n\n{snap_message}"

    if "run_Give Tick Summary" in form_data:
        give_tick_summary(player_tick_summary, full_tick_summary)

    return full_tick_summary

def run_tick_async(form_data):
    """Queue the tick process to run in the background"""
    from threading import Thread
    thread = Thread(target=tick, args=(form_data,))
    thread.daemon = True
    thread.start()
    return "Tick process started in background. Check logs for results."

###########################################################
# General Tick Functions
###########################################################

def backup_database():
    success, message = backup_mongodb_async()
    return success, message

def give_tick_summary(player_tick_summary, full_tick_summary):
    """Save tick summary to a file and optionally email it"""
    print(full_tick_summary)  # Keep console logging
    
    # Create a timestamp for the filename
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # Create summaries directory if it doesn't exist
    summary_dir = os.path.join(os.getcwd(), 'summaries')
    os.makedirs(summary_dir, exist_ok=True)
    
    # Save to file
    player_summary_filename = f"player_tick_summary_{timestamp}.txt"
    player_summary_path = os.path.join(summary_dir, player_summary_filename)
    
    with open(player_summary_path, 'w') as f:
        f.write(player_tick_summary)

    full_summary_filename = f"full_tick_summary_{timestamp}.txt"
    full_summary_path = os.path.join(summary_dir, full_summary_filename)
    
    with open(full_summary_path, 'w') as f:
        f.write(full_tick_summary)
    
    
    # If S3 is configured, upload the summary
    if os.getenv("S3_BUCKET_NAME"):
        upload_to_s3(player_summary_path, f"tick_summaries/{player_summary_filename}")
        upload_to_s3(full_summary_path, f"tick_summaries/{full_summary_filename}")
    
    return full_summary_path

def modifier_decay_tick(old_target, new_target, schema):
    new_modifiers = []
    for modifier in new_target.get("modifiers", []):
        new_modifier = deepcopy(modifier)
        if int(new_modifier.get("duration", -1)) > 0:
            new_modifier["duration"] = int(new_modifier["duration"]) - 1
        if int(new_modifier.get("duration", -1)) != 0:
            new_modifiers.append(new_modifier)
    new_target["modifiers"] = new_modifiers
    return ""

def progress_quests_tick(old_target, new_target, schema):
    for i in range(len(old_target.get("progress_quests", []))):
        new_target["progress_quests"][i]["current_progress"] = old_target["progress_quests"][i].get("current_progress", 0) + old_target["progress_quests"][i].get("total_progress_per_tick", 0)
        if new_target["progress_quests"][i].get("current_progress", 0) >= new_target["progress_quests"][i].get("required_progress", 0):
            new_target["progress_quests"][i]["current_progress"] = new_target["progress_quests"][i].get("required_progress", 0)
            
    return ""

def tick_session_number(old_target, new_target, schema):
    new_target["session_counter"] = old_target.get("session_counter", 0) + 1
    return ""

###########################################################
# Character Tick Functions
###########################################################

RULER_TYPE_STATS = {
    "Steward":          {"strength": "rulership", "weakness": "magic"},
    "Religious Leader": {"strength": "cunning",   "weakness": "strategy"},
    "Populist":         {"strength": "charisma",  "weakness": "prowess"},
    "Conqueror":        {"strength": "prowess",   "weakness": "charisma"},
    "Archmage":         {"strength": "magic",     "weakness": "rulership"},
    "General":          {"strength": "strategy",  "weakness": "cunning"},
}

RULER_SUBTYPES = {
    "Steward":          ["Quartermaster", "Administrator", "Noble"],
    "Religious Leader": ["Prophet", "Martyr", "Guardian"],
    "Populist":         ["Orator", "Diplomat", "Bard"],
    "Conqueror":        ["Duelist", "Champion", "Barbarian"],
    "Archmage":         ["Magus", "Warlock", "Scholar"],
    "General":          ["Tactician", "Tyrant", "Infiltrator"],
}

_RULER_TYPES = list(RULER_TYPE_STATS.keys())


def _pick_succession_title(succession_type, previous_leader):
    """Return a title key based on succession type, or None if nothing is available."""
    positive_titles = json_data.get("positive_titles", {})
    positive_only = {k: v for k, v in positive_titles.items() if v.get("type") == "positive"}
    keys_ordered = list(positive_only.keys())

    # Group consecutive positive titles into lines of 3 (tier 1 → 2 → 3)
    title_lines = [keys_ordered[i:i + 3] for i in range(0, len(keys_ordered), 3)]
    title_to_line = {k: line for line in title_lines for k in line}

    tier1 = [k for k, v in positive_only.items() if v.get("tier") == 1]
    tier3 = [k for k, v in positive_only.items() if v.get("tier") == 3]

    if succession_type == "Elected":
        return random.choice(tier1) if tier1 else None

    if succession_type == "Strength":
        return random.choice(tier3) if tier3 else None

    # Inherited: use previous leader's title line if available
    if previous_leader:
        prev_titles = previous_leader.get("positive_titles", [])
        inherited_line_title = next(
            (t for t in prev_titles if t in title_to_line), None
        )
        if inherited_line_title:
            return title_to_line[inherited_line_title][0]  # tier-1 of that line

    return random.choice(tier1) if tier1 else None


def generate_ai_character(org, org_schema, character_schema, previous_leader=None):
    """Create and insert an AI ruler for the given nation/org. Returns a log string."""
    character_type = random.choice(_RULER_TYPES)
    character_subtype = random.choice(RULER_SUBTYPES[character_type])
    req_strength = RULER_TYPE_STATS[character_type]["strength"]
    req_weakness = RULER_TYPE_STATS[character_type]["weakness"]

    remaining = [s for s in character_stats if s not in (req_strength, req_weakness)]
    rand_strength = random.choice(remaining)
    remaining_for_weakness = [s for s in remaining if s != rand_strength]
    rand_weakness = random.choice(remaining_for_weakness)

    strengths = [req_strength, rand_strength]
    weaknesses = [req_weakness, rand_weakness]

    modifiers = []
    for s in strengths:
        modifiers.append({"field": s, "value": random.randint(2, 4), "duration": -1, "source": "Strength"})
    for w in weaknesses:
        modifiers.append({"field": w, "value": random.randint(-4, -2), "duration": -1, "source": "Weakness"})

    org_name = org.get("name", "Unknown")
    succession_type = org.get("succession_type", "Inherited")

    # Determine ruler demographics and whether to update nation primaries
    ruler_race = str(org["primary_race"]) if org.get("primary_race") else None
    ruler_culture = str(org["primary_culture"]) if org.get("primary_culture") else None
    ruler_religion = str(org["primary_religion"]) if org.get("primary_religion") else None
    pop_selected = None

    if succession_type in ("Elected", "Strength"):
        nation_pops = list(mongo.db.pops.find({"nation": str(org["_id"])}))
        if nation_pops:
            pop_selected = random.choice(nation_pops)
            ruler_race = str(pop_selected["race"]) if pop_selected.get("race") else ruler_race
            ruler_culture = str(pop_selected["culture"]) if pop_selected.get("culture") else ruler_culture
            ruler_religion = str(pop_selected["religion"]) if pop_selected.get("religion") else ruler_religion

    title = _pick_succession_title(succession_type, previous_leader)

    char_props = character_schema.get("properties", {})
    positive_quirk_options = [q for q in char_props.get("positive_quirk", {}).get("enum", []) if q != "None"]
    negative_quirk_options = [q for q in char_props.get("negative_quirk", {}).get("enum", []) if q != "None"]
    positive_quirk = random.choice(positive_quirk_options) if positive_quirk_options else "None"
    negative_quirk = random.choice(negative_quirk_options) if negative_quirk_options else "None"

    magic_points = max(0, sum(m["value"] for m in modifiers if m.get("field") == "magic"))

    base_name = f"{character_type} of {org_name}"
    name = base_name
    counter = 2
    while mongo.db.characters.find_one({"name": name}):
        name = f"{base_name} {counter}"
        counter += 1

    char_doc = {
        "name": name,
        "character_type": character_type,
        "character_subtype": character_subtype,
        "health_status": "Healthy",
        "age_status": "Adult",
        "age": 1,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "modifiers": modifiers,
        "player": None,
        "creator": None,
        "ruling_nation_org": str(org["_id"]),
        "region": str(org["region"]) if org.get("region") else None,
        "race": ruler_race,
        "culture": ruler_culture,
        "religion": ruler_religion,
        "random_stats": 0,
        "positive_titles": [title] if title else [],
        "negative_titles": [],
        "positive_quirk": positive_quirk,
        "negative_quirk": negative_quirk,
        "magic_points": magic_points,
    }

    change_id = system_request_change(
        data_type="characters",
        item_id=None,
        change_type="Add",
        before_data={},
        after_data=char_doc,
        reason=f"Auto-generated AI ruler for {org_name}",
    )
    system_approve_change(change_id)
    new_char_change = mongo.db.changes.find_one({"_id": change_id})
    new_char_id = new_char_change.get("target") if new_char_change else None
    result = f"Generated AI ruler '{name}' ({character_type} / {character_subtype}) for {org_name}.\n"

    if pop_selected:
        new_org = deepcopy(org)
        new_org["primary_race"] = ruler_race
        new_org["primary_culture"] = ruler_culture
        new_org["primary_religion"] = ruler_religion
        change_id = system_request_change(
            data_type="nations",
            item_id=org["_id"],
            change_type="Update",
            before_data=org,
            after_data=new_org,
            reason=f"Succession ({succession_type}): primary demographics updated for {org_name}",
        )
        system_approve_change(change_id)
        result += f"  → Updated {org_name} primary demographics via {succession_type} succession.\n"

    if new_char_id and previous_leader:
        prev_id_str = str(previous_leader["_id"])
        predecessor_artifacts = list(mongo.db.artifacts.find({"owner": prev_id_str, "archived": {"$ne": True}}))
        for artifact in predecessor_artifacts:
            art_change_id = system_request_change(
                data_type="artifacts",
                item_id=artifact["_id"],
                change_type="Update",
                before_data=artifact,
                after_data={"owner": str(new_char_id)},
                reason=f"Artifact inherited by {name} from predecessor",
            )
            system_approve_change(art_change_id)
        if predecessor_artifacts:
            result += f"  → Transferred {len(predecessor_artifacts)} artifact(s) from predecessor.\n"

    return result


def character_death_tick(old_character, new_character, schema):
    result = ""
    if new_character.get("health_status", "Healthy") == "Dead":
        return ""
    death_roll = random.random()
    new_character["death_roll"] = death_roll
    new_character["death_chance_at_tick"] = old_character.get("death_chance", 0)
    if death_roll <= old_character.get("death_chance", 0):
        new_character["health_status"] = "Dead"
        new_character["ruling_nation_org"] = None
        new_character["region"] = None
        new_character["player"] = None
        result = f"{old_character.get('name', 'Unknown')} has died.\n"

        if old_character.get("ruling_nation_org", "") != "":
            nation_schema, nation_db = get_data_on_category("nations")
            try:
                old_nation = nation_db.find_one({"_id": ObjectId(old_character.get("ruling_nation_org", ""))})
            except:
                old_nation = None
            if old_nation:
                old_nation.update(calculate_all_fields(old_nation, nation_schema, "nation"))
                new_nation = deepcopy(old_nation)
                leader_death_stab_loss_roll = random.random()
                new_nation["leader_death_stab_loss_roll"] = leader_death_stab_loss_roll
                new_nation["leader_death_stab_loss_chance_at_tick"] = old_nation.get("stability_loss_chance_on_leader_death", 0)

                amounts = []
                reasons = []

                if leader_death_stab_loss_roll <= old_nation.get("stability_loss_chance_on_leader_death", 0):
                    amounts.append(-1)
                    reasons.append("stability_loss_chance_on_leader_death")
                
                if old_nation.get("stability_loss_chance_on_leader_death_per_age", 0) > 0:
                    stability_loss_chance = min(old_nation.get("stability_loss_chance_on_leader_death_per_age", 0) * old_character.get("age", 1), old_nation.get("max_stability_loss_chance_on_leader_death_per_age", 0))
                    leader_death_age_stab_loss_roll = random.random()

                    new_nation["leader_death_age_stab_loss_roll"] = leader_death_age_stab_loss_roll
                    new_nation["leader_death_age_stab_loss_chance_at_tick"] = stability_loss_chance

                    amount = 0

                    while stability_loss_chance > 1:
                        amount += 1
                        stability_loss_chance -= 1
                    if leader_death_age_stab_loss_roll <= stability_loss_chance:
                        amount += 1
                    if amount > 0:
                        amounts.append(-amount)
                        reasons.append("autocracy_increased_stability_loss_chance_on_leader_death")
                
                result += adjust_stability(old_nation, new_nation, nation_schema, amounts, reasons)

                change_id = system_request_change(
                    data_type="nations",
                    item_id=old_nation["_id"],
                    change_type="Update",
                    before_data=old_nation,
                    after_data=new_nation,
                    reason="Death of " + old_character.get('name', 'Unknown') + " has caused an update for " + old_nation.get('name', 'Unknown')
                )
                system_approve_change(change_id)

                if not old_character.get("player") and not old_nation.get("players"):
                    result += generate_ai_character(old_nation, nation_schema, schema, previous_leader=old_character)

    return result

def character_heal_tick(old_character, new_character, schema):
    result = ""
    if new_character["health_status"] == "Healthy":
        return ""
    if new_character["health_status"] == "Dead":
        return ""
    
    health_status_enum = schema["properties"]["health_status"]["enum"]
    health_index = health_status_enum.index(old_character["health_status"])

    heal_roll = random.random()
    new_character["heal_roll"] = heal_roll
    new_character["heal_chance_at_tick"] = old_character.get("heal_chance", 0)
    if heal_roll <= old_character.get("heal_chance", 0):
        health_index = max(health_index - 1, 0)
        new_character["health_status"] = health_status_enum[health_index]
        result = f"{old_character.get('name', 'Unknown')} has healed from {old_character.get('health_status', 'Unknown')} to {new_character.get('health_status', 'Unknown')}.\n"
    return result

def character_heal_then_death_tick(old_character, new_character, schema):
    result = ""
    if old_character.get("health_status", "Healthy") == "Dead":
        return ""

    # Phase 1: Heal
    healed_to_healthy = False
    if old_character["health_status"] != "Healthy":
        health_status_enum = schema["properties"]["health_status"]["enum"]
        health_index = health_status_enum.index(old_character["health_status"])
        heal_roll = random.random()
        new_character["heal_roll"] = heal_roll
        new_character["heal_chance_at_tick"] = old_character.get("heal_chance", 0)
        if heal_roll <= old_character.get("heal_chance", 0):
            health_index = max(health_index - 1, 0)
            new_character["health_status"] = health_status_enum[health_index]
            result += f"{old_character.get('name', 'Unknown')} has healed from {old_character['health_status']} to {new_character['health_status']}.\n"
            healed_to_healthy = new_character["health_status"] == "Healthy"

    # Phase 2: Death chance — recalculate if healed to Healthy so injury modifier is removed
    if healed_to_healthy:
        recalculated = calculate_all_fields(new_character, schema, "character")
        death_chance = recalculated.get("death_chance", 0)
    else:
        death_chance = old_character.get("death_chance", 0)

    # Phase 3: Death roll
    death_roll = random.random()
    new_character["death_roll"] = death_roll
    new_character["death_chance_at_tick"] = death_chance
    if death_roll <= death_chance:
        new_character["health_status"] = "Dead"
        new_character["ruling_nation_org"] = None
        new_character["region"] = None
        new_character["player"] = None
        result += f"{old_character.get('name', 'Unknown')} has died.\n"

        if old_character.get("ruling_nation_org", ""):
            nation_schema, nation_db = get_data_on_category("nations")
            try:
                old_nation = nation_db.find_one({"_id": ObjectId(old_character.get("ruling_nation_org", ""))})
            except Exception:
                old_nation = None
            if old_nation:
                old_nation.update(calculate_all_fields(old_nation, nation_schema, "nation"))
                new_nation = deepcopy(old_nation)
                leader_death_stab_loss_roll = random.random()
                new_nation["leader_death_stab_loss_roll"] = leader_death_stab_loss_roll
                new_nation["leader_death_stab_loss_chance_at_tick"] = old_nation.get("stability_loss_chance_on_leader_death", 0)

                amounts = []
                reasons = []

                if leader_death_stab_loss_roll <= old_nation.get("stability_loss_chance_on_leader_death", 0):
                    amounts.append(-1)
                    reasons.append("stability_loss_chance_on_leader_death")

                if old_nation.get("stability_loss_chance_on_leader_death_per_age", 0) > 0:
                    stability_loss_chance = min(
                        old_nation.get("stability_loss_chance_on_leader_death_per_age", 0) * old_character.get("age", 1),
                        old_nation.get("max_stability_loss_chance_on_leader_death_per_age", 0)
                    )
                    leader_death_age_stab_loss_roll = random.random()
                    new_nation["leader_death_age_stab_loss_roll"] = leader_death_age_stab_loss_roll
                    new_nation["leader_death_age_stab_loss_chance_at_tick"] = stability_loss_chance

                    amount = 0
                    while stability_loss_chance > 1:
                        amount += 1
                        stability_loss_chance -= 1
                    if leader_death_age_stab_loss_roll <= stability_loss_chance:
                        amount += 1
                    if amount > 0:
                        amounts.append(-amount)
                        reasons.append("autocracy_increased_stability_loss_chance_on_leader_death")

                result += adjust_stability(old_nation, new_nation, nation_schema, amounts, reasons)

                change_id = system_request_change(
                    data_type="nations",
                    item_id=old_nation["_id"],
                    change_type="Update",
                    before_data=old_nation,
                    after_data=new_nation,
                    reason="Death of " + old_character.get('name', 'Unknown') + " has caused an update for " + old_nation.get('name', 'Unknown')
                )
                system_approve_change(change_id)

    return result


def character_mana_tick(old_character, new_character, schema):
    if old_character.get("health_status", "Healthy") == "Dead":
        return ""
    new_character["magic_points"] = min(old_character.get("magic_points", 0) + old_character.get("magic_point_income", 0), old_character.get("magic_point_capacity", 0))
    return ""

def character_age_tick(old_character, new_character, schema):
    if old_character.get("health_status", "Healthy") == "Dead":
        return ""
    new_character["age"] = old_character["age"] + 1
    return ""

def character_stat_gain_tick(old_character, new_character, schema):
    result = ""
    if old_character.get("health_status", "Healthy") == "Dead":
        return ""

    effective_chance = old_character.get("stat_gain_chance", 0)
    is_ai = not old_character.get("player")
    name = old_character.get("name", "Unknown")

    if is_ai:
        cunning = old_character.get("cunning", 0)
        is_immortal = old_character.get("elderly_age", 3) > 500
        effective_chance += cunning * 0.1  # doubles the base cunning contribution
        effective_chance += 0.25 if is_immortal else 0.5
        # no cap for AI — effective_chance above 1.0 guarantees gains

    if effective_chance <= 0:
        return ""

    new_character["stat_gain_chance_at_tick"] = effective_chance

    if is_ai:
        modifiers = new_character.get("modifiers", [])
        new_character["modifiers"] = modifiers
        for stat in character_stats:
            if old_character.get(stat, 0) < old_character.get(stat + "_cap", 4):
                if random.random() <= effective_chance:
                    modifiers.append({"_id": uuid.uuid4().hex[:8], "field": stat, "value": 1, "duration": -1, "source": "Stat gain tick"})
                    result += f"{name} has gained a level of {stat}.\n"
    else:
        stat_gain_roll = random.random()
        new_character["stat_gain_roll"] = stat_gain_roll
        if stat_gain_roll <= effective_chance:
            possible_stats = [s for s in character_stats if old_character.get(s, 0) < old_character.get(s + "_cap", 4)]
            if possible_stats:
                stat = random.choice(possible_stats)
                modifiers = new_character.get("modifiers", [])
                modifiers.append({"_id": uuid.uuid4().hex[:8], "field": stat, "value": 1, "duration": -1, "source": "Stat gain tick"})
                new_character["modifiers"] = modifiers
                result = f"{name} has gained a level of {stat}.\n"

    return result

def artifact_loss_tick(old_character, new_character, schema):
    result = ""
    
    artifact_loss_chance = old_character.get("artifact_loss_chance", 0)
    if artifact_loss_chance <= 0:
        return ""
    artifact_loss_roll = random.random()
    new_character["artifact_loss_roll"] = artifact_loss_roll
    new_character["artifact_loss_chance_at_tick"] = artifact_loss_chance
    if artifact_loss_roll <= artifact_loss_chance:
        artifact_schema, artifact_db = get_data_on_category("artifacts")
        unequipped_artifacts = list(artifact_db.find({"owner": str(old_character.get("_id", "")), "equipped": False}))
        if unequipped_artifacts:
            old_artifact = random.choice(unequipped_artifacts)
            new_artifact = deepcopy(old_artifact)
            new_artifact["owner"] = "Lost"
            result = f"{old_character.get('name', 'Unknown')} has lost {old_artifact.get('name', 'Unknown')}.\n"
            change_id = system_request_change(
                data_type="artifacts",
                item_id=old_artifact["_id"],
                change_type="Update",
                before_data=old_artifact,
                after_data=new_artifact,
                reason=old_artifact.get('name', 'Unknown') + " has been lost due to passive loss chance"
            )
            system_approve_change(change_id)
        else:
            equipped_artifacts = list(artifact_db.find({"owner": str(old_character.get("_id", "")), "equipped": True}))
            if equipped_artifacts:
                old_artifact = random.choice(equipped_artifacts)
                new_artifact = deepcopy(old_artifact)
                new_artifact["owner"] = "Lost"
                result = f"{old_character.get('name', 'Unknown')} has lost {old_artifact.get('name', 'Unknown')}.\n"
                change_id = system_request_change(
                    data_type="artifacts",
                    item_id=old_artifact["_id"],
                    change_type="Update",
                    before_data=old_artifact,
                    after_data=new_artifact,
                    reason="Loss of " + old_character.get('name', 'Unknown') + " has caused " + old_artifact.get('name', 'Unknown') + " to be lost"
                )
                system_approve_change(change_id)
    return result

###########################################################
# Artifact Tick Functions
###########################################################

###########################################################
# Merchant Tick Functions
###########################################################

def merchant_income_tick(old_merchant, new_merchant, schema):
    new_merchant["treasury"] = int(old_merchant.get("treasury", 0)) + old_merchant.get("income", 0)

    new_merchant["resource_storage"] = {}
    for resource, amount in old_merchant.get("resource_production", {}).items():
        new_merchant["resource_storage"][resource] = old_merchant.get("resource_storage", {}).get(resource, 0) + amount
    return ""

###########################################################
# Mercenary Tick Functions
###########################################################

def mercenary_upkeep_tick(old_mercenary, new_mercenary, schema):
    new_mercenary["treasury"] = int(old_mercenary.get("treasury", 0)) - old_mercenary.get("upkeep", 0)
    return ""

###########################################################
# Faction Tick Functions
###########################################################

def faction_income_tick(old_faction, new_faction, schema):
    new_faction["influence"] = int(old_faction.get("influence", 0)) + old_faction.get("influence_income", 0)
    return ""

###########################################################
# Market Tick Functions
###########################################################

def market_income_tick(old_market, new_market, schema):
    new_market["resource_storage"] = {}
    for resource, amount in old_market.get("resource_production", {}).items():
        new_market["resource_storage"][resource] = old_market.get("resource_storage", {}).get(resource, 0) + amount
        new_market["resource_storage"][resource] = min(new_market["resource_storage"][resource], old_market.get("market_resource_capacity", {}).get(resource, 0))
        new_market["resource_storage"][resource] = max(new_market["resource_storage"][resource], 0)
        new_market["resource_storage"][resource] = int(new_market["resource_storage"][resource])
    return ""

###########################################################
# Nation Tick Functions
###########################################################

def isolated_diplo_stance_tick(old_nation, new_nation, schema):
    if old_nation.get("diplomatic_stance", "None") != "Isolated":
        modifiers = new_nation.get("modifiers", [])
        for modifier in modifiers:
            if modifier.get("field", "") == "stability_gain_chance" and modifier.get("source", "") == "Isolated Diplomatic Stance":
                removed_stab_gain = modifier["value"]
                modifiers.remove(modifier)
                new_nation["modifiers"] = modifiers
                old_nation["stability_gain_chance"] -= removed_stab_gain #Remove the stab gain chance because if the nation swapped off Isolated, they should not keep the stab gain chance
                return f"{old_nation.get('name', 'Unknown')} has had the stability gain chance modifier from their Isolated diplomatic stance removed because they are no longer Isolated.\n"
        return ""
    else:
        gain_rate = old_nation.get("isolated_stab_gain_rate", 0)
        cap = old_nation.get("isolated_stab_gain_max", 0)
        print(f"Gain Rate: {gain_rate}, Cap: {cap}")
        modifiers = new_nation.get("modifiers", [])
        for modifier in modifiers:
            if modifier.get("field", "") == "stability_gain_chance" and modifier.get("source", "") == "Isolated Diplomatic Stance":
                old_value = modifier["value"]
                new_value = min(modifier["value"] + gain_rate, cap)
                modifier["value"] = new_value
                new_nation["modifiers"] = modifiers
                return f"{old_nation.get('name', 'Unknown')} has had the stability gain chance modifier from their Isolated diplomatic stance increased from {old_value} to {new_value}.\n"
        modifiers.append({"_id": uuid.uuid4().hex[:8], "field": "stability_gain_chance", "value": max(gain_rate, cap), "duration": -1, "source": "Isolated Diplomatic Stance"})
        new_nation["modifiers"] = modifiers
        return f"{old_nation.get('name', 'Unknown')} has had the stability gain chance modifier from their Isolated diplomatic stance increased from 0 to {max(gain_rate, cap)}.\n"

def ai_resource_desire_tick(old_nation, new_nation, schema):
    if old_nation.get("temperament", "None") == "Player":
        return ""
    general_resources = [resource["key"] for resource in json_data["general_resources"]]
    unique_resources = [resource["key"] for resource in json_data["unique_resources"]]
    luxury_resources = [resource["key"] for resource in json_data["luxury_resources"]]
    common_resources = general_resources + unique_resources
    general_resource_prices = {resource["key"]: resource.get("base_price", 0) for resource in json_data["general_resources"]}
    unique_resource_prices = {resource["key"]: resource.get("base_price", 0) for resource in json_data["unique_resources"]}
    luxury_resource_prices = {resource["key"]: resource.get("base_price", 0) for resource in json_data["luxury_resources"]}
    resource_prices = {**general_resource_prices, **unique_resource_prices, **luxury_resource_prices}
    new_nation["resource_desires"] = []
    for resource in common_resources:
        desire_roll = random.random()
        base_price = resource_prices[resource]
        price_roll = random.random() / 10 #Rolls somewhere between 0 and 10
        trade_type = "None"
        price = 0
        quantity = random.randint(1, 5)  # Random quantity between 1-5
        if desire_roll <= 0.1:
            price = base_price * (1.15 + price_roll)
            trade_type = "Need to Buy"
        elif desire_roll <= 0.25:
            price = base_price * (0.95 + price_roll)
            trade_type = "Desire to Buy"
        elif desire_roll >= 0.75:
            price = base_price * (0.95 + price_roll)
            trade_type = "Desire to Sell"
        elif desire_roll >= 0.9:
            price = base_price * (0.75 + price_roll)
            trade_type = "Need to Sell"
        price = int(round(price / 5)) * 5
        if price != 0:
            new_nation["resource_desires"].append({"resource": resource, "trade_type": trade_type, "price": price, "quantity": quantity})
    
    for resource in luxury_resources:
        desire_roll = random.random()
        base_price = resource_prices[resource]
        price_roll = random.random() / 10 #Rolls somewhere between 0 and 0.1
        trade_type = "None"
        price = 0
        quantity = 1
        if desire_roll <= 0.05:
            price = base_price * (1.15 + price_roll)
            trade_type = "Need to Buy"
        elif desire_roll <= 0.1:
            price = base_price * (0.95 + price_roll)
            trade_type = "Desire to Buy"
        price = int(round(price / 5)) * 5
        if price != 0:
            new_nation["resource_desires"].append({"resource": resource, "trade_type": trade_type, "price": price, "quantity": quantity})
        
    return ""

def nation_income_tick(old_nation, new_nation, schema):
    new_nation["money"] = int(old_nation.get("money", 0)) + old_nation.get("money_income", 0)
    if new_nation["money"] > new_nation.get("money_capacity", 0):
        new_nation["money"] = new_nation.get("money_capacity", 0)
    new_nation["resource_storage"] = {}
    new_nation["production_at_tick"] = old_nation.get("resource_excess", {})
    for resource, amount in old_nation.get("resource_excess", {}).items():
        new_nation["resource_storage"][resource] = min(old_nation.get("resource_storage", {}).get(resource, 0) + amount, old_nation.get("nation_resource_capacity", {}).get(resource, 0))
    
    return ""

def nation_tech_tick(old_nation, new_nation, schema):
    new_nation["research_production_at_tick"] = old_nation.get("resource_production", {}).get("research", 0)
    new_nation["research_consumption_at_tick"] = old_nation.get("resource_consumption", {}).get("research", 0)
    json_tech_data = json_data["tech"]

    techs = old_nation.get("technologies")
    new_nation["technologies"] = deepcopy(techs) if isinstance(techs, dict) else {"political_philosophy": {"researched": True}}
    for tech, value in new_nation["technologies"].items():
        if value.get("investing", 0) > 0:
            value["invested"] = value.get("invested", 0) + value.get("investing", 0)
            value["investing"] = 0
            if value["invested"] >= value.get("cost", json_tech_data.get(tech, {}).get("cost", 0) + old_nation.get("technology_cost_modifier", 0)):
                value["researched"] = True
        new_nation["technologies"][tech] = value
    return ""

def update_rolling_karma(old_nation, new_nation, schema):
    event_type = old_nation.get("event_type", "Unknown")
    if event_type in ["Horrendous", "Abysmal", "Very Bad", "Bad"]:
        new_nation["rolling_karma"] = int(old_nation.get("rolling_karma", 0)) + 1
    elif event_type in ["Good", "Very Good", "Fantastic", "Wonderous"]:
        new_nation["rolling_karma"] = int(old_nation.get("rolling_karma", 0)) - 1

    return ""

def nation_infamy_decay_tick(old_nation, new_nation, schema):
    infamy = int(old_nation.get("infamy", 0))
    if infamy == 0:
        return ""

    global_modifiers = mongo.db["global_modifiers"].find_one({"name": "global_modifiers"})
    current_session = global_modifiers.get("session_counter", 0) if global_modifiers else 0

    nation_id_str = str(old_nation.get("_id", ""))
    attacker_links = list(mongo.db.war_links.find({"participant": nation_id_str, "stance": "Attacker"}))
    for link in attacker_links:
        war = mongo.db.wars.find_one({"_id": ObjectId(link["war"])}) if link.get("war") else None
        if war:
            session_declared = war.get("session_declared", 0)
            session_ended = war.get("session_ended", None)
            if session_declared <= current_session and (session_ended is None or session_ended >= current_session):
                new_nation["infamy"] = infamy
                return ""

    decay = max(min(math.floor((infamy / 2) / 5) * 5, 20), 5)
    new_nation["infamy"] = max(0, infamy - decay)
    return ""

def nation_prestige_gain_tick(old_nation, new_nation, schema):
    if not old_nation.get("empire", False):
        return ""
    old_nation_prestige = old_nation.get("prestige", 0)
    if old_nation_prestige == "":
        old_nation_prestige = 0
    new_nation["prestige"] = old_nation_prestige + old_nation.get("prestige_gain", 0)
    new_nation["prestige"] = min(max(new_nation["prestige"], 0), 100)
    return ""

def nation_civil_war_tick(old_nation, new_nation, schema):
    if old_nation.get("civil_war_chance", 0) == 0:
        return ""
    civil_war_roll = random.random()
    new_nation["passive_civil_war_roll"] = civil_war_roll
    new_nation["passive_civil_war_chance_at_tick"] = old_nation.get("civil_war_chance", 0)
    if civil_war_roll <= old_nation.get("civil_war_chance", 0):
        new_nation["stability"] = "Unsettled"
        return f"{old_nation.get('name', 'Unknown')} has experienced a civil war due to passive civil war chance.\n"
    return ""


def nation_stability_tick(old_nation, new_nation, schema):
    result = ""

    stability_gained = 0
    stability_lost = 0

    stability_gain_roll = random.random()
    new_nation["stability_gain_roll"] = stability_gain_roll
    new_nation["stability_gain_chance_at_tick"] = old_nation.get("stability_gain_chance", 0)

    while old_nation.get("stability_gain_chance", 0) > 1:
        stability_gained += 1
        old_nation["stability_gain_chance"] -= 1

    if stability_gain_roll <= old_nation.get("stability_gain_chance", 0):
        stability_gained += 1

    stab_loss_chance = old_nation.get("stability_loss_chance", 0)
    stability_loss_roll = random.random()
    new_nation["stability_loss_roll"] = stability_loss_roll
    new_nation["stability_loss_chance_at_tick"] = stab_loss_chance

    while stab_loss_chance > 1:
        stability_lost += 1
        stab_loss_chance -= 1

    if stability_loss_roll <= stab_loss_chance:
        stability_lost += 1

    amounts = []
    reasons = []

    if stability_gained > 0:
        amounts.append(stability_gained)
        reasons.append("stability_gain_chance")
    if stability_lost > 0:
        amounts.append(-stability_lost)
        reasons.append("stability_loss_chance")

    result += adjust_stability(old_nation, new_nation, schema, amounts, reasons)

    return result

def nation_concessions_tick(old_nation, new_nation, schema):
    if old_nation["overlord"] == "":
        return ""

    result = ""

    if old_nation.get("concessions", {}) and old_nation.get("concessions", {}) != {} and old_nation.get("concessions", {}) != "":
        new_nation["concessions"] = {}
        compliance_enum = schema["properties"]["compliance"]["enum"]
        compliance_index = compliance_enum.index(old_nation["compliance"])
        new_nation["compliance"] = compliance_enum[compliance_index - 1]
        result += f"{old_nation.get('name', 'Unknown')} has had their compliance reduced from {old_nation.get('compliance', 'Unknown')} to {new_nation.get('compliance', 'Unknown')} due to concessions not being paid.\n"

    if old_nation.get("concessions_roll", 1) < old_nation.get("concessions_chance_at_tick", 0):
        new_nation["concessions_roll"] = 1
        new_nation["concessions_chance_at_tick"] = 0
        return result

    concessions_roll = random.random()
    new_nation["concessions_roll"] = concessions_roll
    new_nation["concessions_chance_at_tick"] = old_nation.get("concessions_chance", 0)
    if concessions_roll <= old_nation.get("concessions_chance", 0):
        concessions_qty = old_nation.get("concessions_qty", 0)
        resources = []
        for resource in json_data["general_resources"]:
            if resource["key"] != "research":
                resources.append(resource["key"])
        for resource in json_data["unique_resources"]:
            resources.append(resource["key"])
        
        first_resource = random.choice(resources)
        resources.remove(first_resource)
        second_resource = random.choice(resources)
        first_amount = random.randint(1, concessions_qty - 1)
        second_amount = concessions_qty - first_amount

        new_nation["concessions"] = {
            first_resource: first_amount,
            second_resource: second_amount
        }

        result += f"{old_nation.get('name', 'Unknown')} has demanded concessions from their overlord.\n"
    return result

def nation_rebellion_tick(old_nation, new_nation, schema):
    if old_nation["overlord"] == "":
        return ""
    result = ""
    rebellion_roll = random.random()
    new_nation["rebellion_roll"] = rebellion_roll
    new_nation["rebellion_chance_at_tick"] = old_nation.get("rebellion_chance", 0)
    if rebellion_roll <= old_nation.get("rebellion_chance", 0):
        result += f"{old_nation.get('name', 'Unknown')} has rebelled against their overlord.\n"

    return result

def nation_vassal_compliance_decay_tick(old_nation, new_nation, schema):
    if not old_nation.get("overlord"):
        return ""
    compliance = old_nation.get("compliance", "None")
    decay_chances = {"Loyal": 0.15, "Compliant": 0.10}
    chance = decay_chances.get(compliance, 0)
    if chance <= 0:
        return ""
    if random.random() <= chance:
        compliance_enum = schema["properties"]["compliance"]["enum"]
        idx = compliance_enum.index(compliance)
        new_compliance = compliance_enum[idx - 1]
        new_nation["compliance"] = new_compliance
        return f"{old_nation.get('name', 'Unknown')}'s compliance fell from {compliance} to {new_compliance}.\n"
    return ""


def nation_enclave_compliance_tick(old_nation, new_nation, schema):
    if not old_nation.get("overlord") or old_nation.get("vassal_type") != "Enclave":
        return ""
    compliance = old_nation.get("compliance", "None")
    if compliance == "None" or compliance == "Rebellious":
        return ""
    try:
        overlord = mongo.db.nations.find_one(
            {"_id": ObjectId(old_nation["overlord"])}, {"primary_religion": 1}
        )
    except Exception:
        return ""
    if not overlord:
        return ""
    vassal_religion = str(old_nation.get("primary_religion") or "")
    overlord_religion = str(overlord.get("primary_religion") or "")
    if not overlord_religion or vassal_religion == overlord_religion:
        return ""
    compliance_enum = schema["properties"]["compliance"]["enum"]
    idx = compliance_enum.index(compliance)
    new_compliance = compliance_enum[max(1, idx - 1)]
    new_nation["compliance"] = new_compliance
    return (
        f"{old_nation.get('name', 'Unknown')}'s compliance fell from {compliance} to {new_compliance}"
        f" due to religious differences with their overlord.\n"
    )



def nation_passive_expansion_tick(old_nation, new_nation, schema):
    from helpers.hex_map_helpers import select_passive_expansion_tiles

    result = ""
    expansion_rolls = 1
    if old_nation.get("temperament", "None") != "Player":
        global_modifiers = mongo.db["global_modifiers"].find_one({"name": "global_modifiers"})
        current_session = global_modifiers.get("session_counter", 1)
        if current_session % 5 == 0:
            expansion_rolls = 5
        else:
            expansion_rolls = 0

    if expansion_rolls <= 0:
        return result

    expansion_chance = old_nation.get("passive_expansion_chance", 0)
    new_nation["expansion_chance_at_tick"] = expansion_chance

    # Count how many rolls succeed
    successes = 0
    last_roll = 0.0
    for _ in range(expansion_rolls):
        roll = random.random()
        last_roll = roll
        if roll <= expansion_chance:
            successes += 1
    new_nation["expansion_roll"] = last_roll

    if successes == 0:
        return result

    nation_name = old_nation.get("name", "Unknown")

    # Fetch tiles once; reuse across multiple successful rolls so that tiles
    # claimed in earlier rounds are visible to later rounds.
    all_tiles = list(mongo.db.hex_map_tiles.find(
        {},
        {"q": 1, "r": 1, "terrain": 1, "owner": 1,
         "city": 1, "district": 1, "wonder": 1, "capital": 1,
         "portal": 1, "route": 1, "_id": 0},
    ))
    tile_map = {(t["q"], t["r"]): t for t in all_tiles}

    # For each successful roll, select and claim tiles
    claimed = []
    for _ in range(successes):
        to_claim = select_passive_expansion_tiles(old_nation, all_tiles)
        if not to_claim:
            break
        for (q, r) in to_claim:
            mongo.db.hex_map_tiles.update_one(
                {"q": q, "r": r},
                {"$set": {"owner": nation_name}}
            )
            claimed.append((q, r))
            # Update the in-memory tile so subsequent rounds see the new ownership
            if (q, r) in tile_map:
                tile_map[(q, r)]["owner"] = nation_name

    if claimed:
        # Resync territory_types on the nation document
        pipeline = [
            {"$match": {"owner": nation_name, "terrain": {"$exists": True, "$ne": None}}},
            {"$group": {"_id": "$terrain", "count": {"$sum": 1}}},
        ]
        counts = {doc["_id"]: doc["count"]
                  for doc in mongo.db.hex_map_tiles.aggregate(pipeline)}
        mongo.db.nations.update_one(
            {"name": nation_name},
            {"$set": {"territory_types": counts}}
        )
        result += f"{nation_name} expanded into {len(claimed)} tile(s).\n"
        from helpers.hex_map_helpers import bump_tile_version
        bump_tile_version()

    return result

def nation_job_cleanup_tick(old_nation, new_nation, schema):
    new_jobs = {}
    for job in old_nation.get("jobs", {}).keys():
        if job != "undead" and job != "partial_vampire" and job != "revolutionary":
            new_jobs[job] = 0
    new_nation["jobs"] = new_jobs
    return ""

def vampirism_tick(old_nation, new_nation, schema):
    if old_nation.get("vampirism_chance", 0) <= 0:
        return ""
    result = ""
    vampirism_roll = random.random()
    new_nation["vampirism_roll"] = vampirism_roll
    new_nation["vampirism_chance_at_tick"] = old_nation.get("vampirism_chance", 0)
    if vampirism_roll <= old_nation.get("vampirism_chance", 0):
        new_nation["jobs"]["partial_vampire"] = old_nation.get("jobs", {}).get("partial_vampire", 0) + 1
        result += f"{old_nation.get('name', 'Unknown')} has gained a vampire.\n"
    return result

def undead_tick(old_nation, new_nation, schema):
    if old_nation.get("undead_chance", 0) <= 0:
        return ""
    result = ""
    undead_roll = random.random()
    new_nation["undead_roll"] = undead_roll
    new_nation["undead_chance_at_tick"] = old_nation.get("undead_chance", 0)
    if undead_roll <= old_nation.get("undead_chance", 0):
        new_nation["jobs"]["partial_undead"] = old_nation.get("jobs", {}).get("partial_undead", 0) + 1
        result += f"{old_nation.get('name', 'Unknown')} has gained an undead.\n"
        double_undead_roll = random.random()
        new_nation["double_undead_roll"] = double_undead_roll
        if double_undead_roll <= 0.25:
            result += f"{old_nation.get('name', 'Unknown')} spread an undead to a nearby nation.\n"
    return result

def pop_loss_tick(old_nation, new_nation, schema):
    result = ""
    if old_nation.get("pop_loss_chance", 0) <= 0:
        return ""
    pop_loss_roll = random.random()
    new_nation["pop_loss_roll"] = pop_loss_roll
    new_nation["pop_loss_chance_at_tick"] = old_nation.get("pop_loss_chance", 0)
    if pop_loss_roll <= old_nation.get("pop_loss_chance", 0):
        result += f"{old_nation.get('name', 'Unknown')} has lost a pop.\n"
    return result

def pop_flee_tick(old_nation, new_nation, schema):
    """Roll nation's pop_flee_chance once; on success one excess pop flees to a random non-Closed nation."""
    pop_count   = old_nation.get("pop_count", 0)
    eff_cap     = old_nation.get("effective_pop_capacity", 0)
    excess_pops = max(0, pop_count - eff_cap)
    if excess_pops <= 0:
        return ""

    flee_chance = old_nation.get("pop_flee_chance", 0.0)
    if flee_chance <= 0 or random.random() > flee_chance:
        return ""

    region_id = str(old_nation.get("region", ""))
    if not region_id:
        return ""

    try:
        candidates = list(mongo.db.nations.find(
            {
                "region": region_id,
                "_id": {"$ne": old_nation["_id"]},
                "citizenship_stance": {"$ne": "Closed"},
            },
            {"_id": 1, "name": 1},
        ))
    except Exception:
        return ""

    if not candidates:
        return ""

    nation_id_str = str(old_nation["_id"])
    try:
        pops = list(mongo.db.pops.find(
            {"nation": nation_id_str},
            {"_id": 1, "race": 1, "culture": 1, "religion": 1},
        ))
    except Exception:
        return ""

    if not pops:
        return ""

    fleeing_pop  = random.choice(pops)
    destination  = random.choice(candidates)
    old_pop_data = {k: v for k, v in fleeing_pop.items() if k != "_id"}
    new_pop_data = dict(old_pop_data)
    new_pop_data["nation"] = str(destination["_id"])

    change_id = system_request_change(
        data_type="pops",
        item_id=fleeing_pop["_id"],
        change_type="Update",
        before_data=old_pop_data,
        after_data=new_pop_data,
        reason=(
            f"Pop fled from {old_nation.get('name', 'Unknown')} "
            f"to {destination.get('name', 'Unknown')} due to overcrowding"
        ),
    )
    if change_id:
        system_approve_change(change_id)
        return (
            f"A pop fled from {old_nation.get('name', 'Unknown')} "
            f"to {destination.get('name', 'Unknown')} due to overcrowding.\n"
        )
    return ""

def temperament_tick(old_nation, new_nation, schema):
    if old_nation.get("temperament", "None") == "Player":
        return ""
    result = ""
    sessions_since_temperament_change = old_nation.get("sessions_since_temperament_change", 1)
    chance_of_temperament_change = sessions_since_temperament_change * 0.25
    temperament_change_roll = random.random()
    new_nation["temperament_change_roll"] = temperament_change_roll
    new_nation["temperament_change_chance_at_tick"] = chance_of_temperament_change
    if temperament_change_roll <= chance_of_temperament_change:
        try:
            culture = mongo.db.cultures.find_one({"_id": ObjectId(old_nation.get("primary_culture", ""))})
        except:
            culture = None
        trait_1_modifier = {}
        trait_2_modifier = {}
        trait_3_modifier = {}
        if culture:
            trait_1 = culture.get("trait_one", "None")
            trait_2 = culture.get("trait_two", "None")
            trait_3 = culture.get("trait_three", "None")

            trait_1_modifier = cultural_trait_temperament_modifiers.get(trait_1, {})
            trait_2_modifier = cultural_trait_temperament_modifiers.get(trait_2, {})
            trait_3_modifier = cultural_trait_temperament_modifiers.get(trait_3, {})
        
        temperament_odds = base_temperament_odds.copy()
        for temperament in temperament_enum:
            temperament_odds[temperament] += trait_1_modifier.get(temperament, 0)
            temperament_odds[temperament] += trait_2_modifier.get(temperament, 0)
            temperament_odds[temperament] += trait_3_modifier.get(temperament, 0)
        
        temperament_roll = random.random()
        new_nation["temperament_roll"] = temperament_roll
        new_nation["temperament_odds"] = temperament_odds
        cumulative_odds = 0
        for temperament in temperament_enum:
            cumulative_odds += temperament_odds[temperament]
            if temperament_roll <= cumulative_odds:
                new_nation["temperament"] = temperament
                result += f"{old_nation.get('name', 'Unknown')} has changed their temperament to {temperament}.  It had been {sessions_since_temperament_change} sessions since their last temperament change\n"
                break

        new_nation["sessions_since_temperament_change"] = 1
    else:
        new_nation["sessions_since_temperament_change"] = sessions_since_temperament_change + 1
    
    return result

def nation_tech_cost_reduction_tick(old_nation, new_nation, schema):
    result = ""
    json_tech_data = json_data["tech"]
    
    techs = new_nation.get("technologies") or {}
    for tech, value in (techs.items() if isinstance(techs, dict) else []):
        base_cost = json_tech_data.get(tech, {}).get("cost", 0)
        current_cost = value.get("cost", base_cost + old_nation.get("technology_cost_modifier", 0))
        invested = value.get("invested", 0)
        
        # Reduce cost by 1 if it's higher than base cost and at least 2 higher than invested
        if current_cost > base_cost and current_cost >= invested + 2:
            value["cost"] = current_cost - 1
            result += f"{old_nation.get('name', 'Unknown')} has reduced the cost of {tech} from {current_cost} to {current_cost - 1}.\n"
        
        new_nation["technologies"][tech] = value
    
    return result

def reset_rolling_karma_to_zero(old_nation, new_nation, schema):
    if old_nation.get("technologies", {}).get("cultural_prophecy", {}).get("researched", False):
        new_nation["rolling_karma"] = 2
    else:
        new_nation["rolling_karma"] = 0
    return ""

def reset_all_temperaments(old_nation, new_nation, schema):
    if old_nation.get("temperament", "None") == "Player":
        return ""
    result = ""
    try :
        culture = mongo.db.cultures.find_one({"_id": ObjectId(old_nation.get("primary_culture", ""))})
    except:
        culture = None
    trait_1_modifier = {}
    trait_2_modifier = {}
    trait_3_modifier = {}
    if culture:
        trait_1 = culture.get("trait_one", "None")
        trait_2 = culture.get("trait_two", "None")
        trait_3 = culture.get("trait_three", "None")

        trait_1_modifier = cultural_trait_temperament_modifiers.get(trait_1, {})
        trait_2_modifier = cultural_trait_temperament_modifiers.get(trait_2, {})
        trait_3_modifier = cultural_trait_temperament_modifiers.get(trait_3, {})

    temperament_odds = base_temperament_odds.copy()
    for temperament in temperament_enum:
        temperament_odds[temperament] += trait_1_modifier.get(temperament, 0)
        temperament_odds[temperament] += trait_2_modifier.get(temperament, 0)
        temperament_odds[temperament] += trait_3_modifier.get(temperament, 0)
    
    temperament_roll = random.random()
    new_nation["temperament_roll"] = temperament_roll
    new_nation["temperament_odds"] = temperament_odds
    cumulative_odds = 0
    for temperament in temperament_enum:
        cumulative_odds += temperament_odds[temperament]
        if temperament_roll <= cumulative_odds:
            new_nation["temperament"] = temperament
            result += f"{old_nation.get('name', 'Unknown')} has changed their temperament to {temperament}.\n"
            break

    new_nation["sessions_since_temperament_change"] = 1
    return result

def empire_prestige_decay_tick(old_nation, new_nation, schema):
    if not old_nation.get("empire"):
        return ""
    current = old_nation.get("empire_prestige_decay", 0)
    new_nation["empire_prestige_decay"] = current + 1
    return ""


def district_duration_tick(old_nation, new_nation, schema):
    """Increment session counters for districts that have the district_duration modifier.

    For each district with def_key that has a district_duration modifier in its
    definition, finds or creates a nation-level modifier tracking sessions:
      {"field": "district_sessions_{def_key}", "value": N, "duration": -1,
       "source": "District: {display_name}"}
    If the nation no longer has the district, the counter modifier is removed.
    """
    from calculations.field_calculations import _resolve_def

    districts = new_nation.get("districts", [])
    modifier_types_data = json_data.get("modifier_types", {})
    modifiers = list(new_nation.get("modifiers", []))
    result = ""

    active_def_keys = {}
    for d in districts:
        if not isinstance(d, dict):
            continue
        dk = d.get("def_key", "")
        if not dk:
            continue
        dd = _resolve_def(d)
        if not dd:
            continue
        has_duration = any(
            modifier_types_data.get(m.get("modifier_type", ""), {}).get("is_district_duration")
            for m in dd.get("modifiers", [])
            if isinstance(m, dict)
        )
        if has_duration:
            active_def_keys[dk] = dd.get("display_name", dk)

    for dk, display_name in active_def_keys.items():
        field_key = f"district_sessions_{dk}"
        source = f"District: {display_name}"
        found = False
        for m in modifiers:
            if m.get("field") == field_key and m.get("source") == source:
                m["value"] = m.get("value", 0) + 1
                found = True
                result += f"{old_nation.get('name', '?')}: {display_name} session count → {m['value']}\n"
                break
        if not found:
            modifiers.append({"field": field_key, "value": 1, "duration": -1, "source": source})
            result += f"{old_nation.get('name', '?')}: {display_name} session count → 1 (new)\n"

    stale = []
    for i, m in enumerate(modifiers):
        f = m.get("field", "")
        if f.startswith("district_sessions_") and f[len("district_sessions_"):] not in active_def_keys:
            stale.append(i)
    for i in reversed(stale):
        removed = modifiers.pop(i)
        result += f"{old_nation.get('name', '?')}: removed stale counter for {removed.get('source', '?')}\n"

    new_nation["modifiers"] = modifiers
    return result

###########################################################
# Era / Age Tick Functions
###########################################################

_RELATION_STEPS = ["Hostile", "Unfriendly", "Neutral", "Friendly", "Allied"]
_COMPLIANCE_STEPS = ["Rebellious", "Defiant", "Neutral", "Compliant", "Loyal"]


def era_reset_stability_to_balanced_tick(old_nation, new_nation, schema):
    new_nation["stability"] = "Balanced"
    return ""


def era_compliance_decay_tick(old_nation, new_nation, schema):
    current = old_nation.get("compliance", "None")
    if current == "None" or current not in _COMPLIANCE_STEPS or current == "Neutral":
        return ""
    idx = _COMPLIANCE_STEPS.index(current)
    neutral_idx = _COMPLIANCE_STEPS.index("Neutral")
    new_nation["compliance"] = _COMPLIANCE_STEPS[idx + 1 if idx < neutral_idx else idx - 1]
    return ""


def era_resource_stockpile_decay_tick(old_nation, new_nation, schema):
    storage = old_nation.get("resource_storage") or {}
    if not storage:
        return ""
    base_kept = random.uniform(0.4, 0.6)
    stockpile_kept = old_nation.get("era_resource_stockpile_kept") or {}
    all_bonus = stockpile_kept.get("resource", 0)
    new_storage = {}
    for resource, amount in storage.items():
        if not isinstance(amount, (int, float)) or amount <= 0:
            new_storage[resource] = amount
            continue
        per_resource_bonus = stockpile_kept.get(resource, 0)
        kept_pct = min(base_kept + all_bonus + per_resource_bonus, 1.0)
        new_storage[resource] = round(amount * kept_pct)
    new_nation["resource_storage"] = new_storage
    kept_pct_display = round((base_kept + all_bonus) * 100)
    return f"{old_nation.get('name', 'Unknown')} kept ~{kept_pct_display}% of stockpile after era decay.\n"


def era_formal_storage_bonus_tick(old_nation, new_nation, schema):
    if not old_nation.get("technologies", {}).get("formal_storage", {}).get("researched", False):
        return ""

    general_resources = [r["key"] for r in json_data["general_resources"] if r["key"] not in ("research", "gunpowder")]
    unique_resources = [r["key"] for r in json_data["unique_resources"]]
    resource_pool = general_resources + unique_resources

    if not resource_pool:
        return ""

    gained = {}
    for _ in range(5):
        resource = random.choice(resource_pool)
        gained[resource] = gained.get(resource, 0) + 1

    storage = new_nation.get("resource_storage") or {}
    capacity = old_nation.get("nation_resource_capacity", {})
    for resource, amount in gained.items():
        current = storage.get(resource, 0)
        cap = capacity.get(resource, 0)
        storage[resource] = min(current + amount, cap) if cap else current + amount
    new_nation["resource_storage"] = storage

    gained_str = ", ".join(f"{v} {k}" for k, v in gained.items())
    return f"{old_nation.get('name', 'Unknown')} gained {gained_str} from Formal Storage.\n"


# ---------------------------------------------------------------------------
# Era AI resource grant
# ---------------------------------------------------------------------------

# Base grants calibrated to a ~15-pop nation (equivalent to a small player's district refunds).
# Scaled linearly by pop count at runtime: 5 pops ≈ 0.33×, 15 pops ≈ 1×, 30 pops ≈ 2×.
_ERA_AI_BASE_GRANTS = {
    "food":   9,
    "wood":   35,
    "stone":  33,
    "mounts": 1,
    "magic":  7,
    "iron":   3,
}
_ERA_AI_REFERENCE_POPS = 15  # pop count that yields the base amounts above


def _era_ai_terrain_weights(nation):
    """Return per-resource weight multipliers [0.5, 2.5] from terrain + node composition."""
    terrain_json = json_data.get("terrains", {})

    # Use effective territory types (what actually produces resources) rather than raw
    # territory_types, which can include disconnected tiles that generate nothing.
    # effective_territory_types is computed by calculate_all_fields and stored on the doc;
    # _calc_cache.effective_territory_types is the same value set during the tick run.
    cache = nation.get("_calc_cache", {}) or {}
    territory = (
        cache.get("effective_territory_types")
        or nation.get("effective_territory_types")
        or nation.get("territory_types")
        or {}
    )
    total_tiles = max(sum(territory.values()), 1)

    # Count tiles producing each resource using the terrain's own production rules
    tiles_by_resource = {}
    for terrain, count in territory.items():
        res = terrain_json.get(terrain, {}).get("resource", "none")
        if res != "none":
            tiles_by_resource[res] = tiles_by_resource.get(res, 0) + count

    # Multiplier: 0.5 (zero tiles) → 2.0 when ≥20% of effective tiles produce this resource
    weights = {}
    for res in _ERA_AI_BASE_GRANTS:
        frac = tiles_by_resource.get(res, 0) / total_tiles
        weights[res] = 0.5 + 1.5 * min(1.0, frac * 5)

    # Node boosts — prefer _calc_cache counts (set by calculate_all_fields during tick),
    # fall back to the stored resource_nodes field on the document.
    nodes = nation.get("nodes", {}) or {}
    territory_nodes = (
        cache.get("territory_node_counts")
        or nation.get("resource_nodes")
        or {}
    )
    for res in _ERA_AI_BASE_GRANTS:
        total_nodes = nodes.get(res, 0) + territory_nodes.get(res, 0)
        if total_nodes > 0:
            weights[res] = min(2.5, weights[res] + 0.25 * total_nodes)

    return weights


def era_ai_resource_grant_tick(old_nation, new_nation, schema):
    """
    Grant AI nations era-transition resources equivalent to what player nations
    received as district refunds.  Amount scales linearly with pop count
    (calibrated so 15 pops ≈ a small player's refund total; 5-pop nations get ~0.33×,
    30-pop nations get ~2×).  Distribution is skewed by terrain composition and nodes.
    """
    if old_nation.get("temperament", "Player") == "Player":
        return ""

    pop_count = max(1, int(old_nation.get("pop_count", 0) or 0))
    scale = pop_count / _ERA_AI_REFERENCE_POPS

    terrain_weights = _era_ai_terrain_weights(old_nation)
    storage = {}  # reset stockpile to zero before granting era resources
    new_nation["resource_storage"] = storage
    capacity = old_nation.get("nation_resource_capacity", {})
    grants = {}

    for res, base_amt in _ERA_AI_BASE_GRANTS.items():
        mult = terrain_weights.get(res, 1.0)
        amount = max(0, round(base_amt * scale * mult * random.uniform(0.75, 1.25)))
        if amount == 0:
            continue
        current = storage.get(res, 0)
        cap = capacity.get(res, 0)
        new_val = min(current + amount, cap) if cap else current + amount
        actual = new_val - current
        if actual > 0:
            storage[res] = new_val
            grants[res] = actual

    new_nation["resource_storage"] = storage

    # Money: skewed toward stone/iron terrain (mines, mints)
    money_mult = (terrain_weights.get("stone", 1.0) + terrain_weights.get("iron", 1.0)) / 2.0
    money_grant = round(random.uniform(0, 1100) * scale * money_mult)
    if money_grant > 0:
        new_nation["money"] = new_nation.get("money", 0) + money_grant
        grants["money"] = money_grant

    if not grants:
        return ""
    summary = ", ".join(f"{k}+{v}" for k, v in sorted(grants.items()))
    return f"{old_nation.get('name', '?')}: era resource grant [{summary}]\n"


def era_relations_decay_tick():
    neutral_idx = _RELATION_STEPS.index("Neutral")
    relations = list(mongo.db.diplo_relations.find())
    count = 0
    for relation in relations:
        current = relation.get("relation", "Neutral")
        if current == "Neutral" or current not in _RELATION_STEPS:
            continue
        idx = _RELATION_STEPS.index(current)
        new_val = _RELATION_STEPS[idx + 1 if idx < neutral_idx else idx - 1]
        change_id = system_request_change(
            data_type="diplo_relations",
            item_id=relation["_id"],
            change_type="Update",
            before_data={"relation": current},
            after_data={"relation": new_val},
            reason="Era Tick: Relations Decay to Neutral",
        )
        if change_id:
            system_approve_change(change_id)
            count += 1
    return f"Decayed {count} relation(s) toward Neutral.\n"


def _era_pop_growth_tick_impl(skip_infertile=False):
    from helpers.admin_tool_helpers import grow_population
    from helpers.hex_map_helpers import get_nations_within_distance

    _, db = get_data_on_category("nations")
    nations = list(db.find().sort("name", ASCENDING))
    count = 0

    for nation in nations:
        if skip_infertile:
            race_id = nation.get("primary_race")
            if race_id:
                try:
                    race = mongo.db.races.find_one(
                        {"_id": ObjectId(race_id)}, {"negative_trait": 1, "_id": 0}
                    )
                    if race and race.get("negative_trait") == "Infertile":
                        continue
                except Exception:
                    pass

        nearby_names = get_nations_within_distance(nation["name"], max_distance=10)
        foreign_nation = None
        if nearby_names:
            chosen_name = random.choice(nearby_names)
            foreign_nation = db.find_one({"name": chosen_name})

        grow_population(nation, foreign_nation)
        count += 1

    return count


def era_pop_growth_tick():
    count = _era_pop_growth_tick_impl(skip_infertile=False)
    return f"Era Pop Growth: grew {count} nation(s).\n"


def age_pop_growth_tick():
    count = _era_pop_growth_tick_impl(skip_infertile=True)
    return f"Age Pop Growth: grew {count} nation(s) (infertile races skipped).\n"


def era_artifact_loss_tick():
    """Roll artifact loss chance 3 times per character; lose 1 artifact per successful roll."""
    character_schema, character_db = get_data_on_category("characters")
    _, artifact_db = get_data_on_category("artifacts")

    characters = list(character_db.find().sort("name", ASCENDING))
    losses_log = ""

    for character in characters:
        if character.get("health_status", "Healthy") == "Dead":
            continue

        character.update(calculate_all_fields(character, character_schema, "character"))
        artifact_loss_chance = character.get("artifact_loss_chance", 0)
        if artifact_loss_chance <= 0:
            continue

        losses = sum(1 for _ in range(3) if random.random() <= artifact_loss_chance)
        if losses <= 0:
            continue

        char_id_str = str(character["_id"])
        unequipped = list(artifact_db.find({"owner": char_id_str, "equipped": False}))
        equipped = list(artifact_db.find({"owner": char_id_str, "equipped": True}))
        available = unequipped + equipped  # prefer losing unequipped first

        for _ in range(losses):
            if not available:
                break
            pool = [a for a in available if not a.get("equipped", False)] or available
            old_artifact = random.choice(pool)
            new_artifact = deepcopy(old_artifact)
            new_artifact["owner"] = "Lost"
            available.remove(old_artifact)

            change_id = system_request_change(
                data_type="artifacts",
                item_id=old_artifact["_id"],
                change_type="Update",
                before_data=old_artifact,
                after_data=new_artifact,
                reason=f"{old_artifact.get('name', 'Unknown')} lost by {character.get('name', 'Unknown')} during era artifact loss",
            )
            system_approve_change(change_id)
            losses_log += f"  {character.get('name', 'Unknown')} lost {old_artifact.get('name', 'Unknown')}.\n"

    if losses_log:
        return f"Era Artifact Loss:\n{losses_log}"
    return "Era Artifact Loss: no artifacts lost.\n"


def era_character_aging_tick():
    """Roll 5d4 once; age every living character by that many sessions.
    Any character whose age exceeds their elderly_age threshold by more than 2 dies."""
    character_schema, character_db = get_data_on_category("characters")

    age_increase = sum(random.randint(1, 4) for _ in range(5))
    result = f"Era Character Aging: all characters age by {age_increase} sessions.\n"

    characters = list(character_db.find().sort("name", ASCENDING))

    for character in characters:
        if character.get("health_status", "Healthy") == "Dead":
            continue

        character.update(calculate_all_fields(character, character_schema, "character"))
        new_character = deepcopy(character)

        new_age = character.get("age", 1) + age_increase
        new_character["age"] = new_age

        elderly_age = character.get("elderly_age", 3)
        if new_age > elderly_age + 2:
            new_character["health_status"] = "Dead"
            new_character["ruling_nation_org"] = None
            new_character["region"] = None
            new_character["player"] = None
            result += (
                f"{character.get('name', 'Unknown')} died of old age"
                f" (age {new_age}, elderly threshold {elderly_age}).\n"
            )

        change_id = system_request_change(
            data_type="characters",
            item_id=character["_id"],
            change_type="Update",
            before_data=character,
            after_data=new_character,
            reason=f"Era Tick: aged {age_increase} session(s)",
        )
        system_approve_change(change_id)

    return result


###########################################################
# Tick Function Constants
###########################################################

GENERAL_TICK_FUNCTIONS = {
    "Backup Database": backup_database,
    "Give Tick Summary": give_tick_summary,
    "Tick Session Number": tick_session_number,
    "Snapshot Hex Map": None,   # handled directly in tick() after session number is committed
}

CHARACTER_TICK_FUNCTIONS = {
    "Character Heal and Death Tick": character_heal_then_death_tick,
    "Character Mana Tick": character_mana_tick,
    "Character Age Tick": character_age_tick,
    "Character Stat Gain Tick": character_stat_gain_tick,
    "Character Modifier Decay Tick": modifier_decay_tick,
    "Character Progress Quests Tick": progress_quests_tick,
    "Character Artifact Loss Tick": artifact_loss_tick,
}

ARTIFACT_TICK_FUNCTIONS = {
}

MERCHANT_TICK_FUNCTIONS = {
    "Merchant Income Tick": merchant_income_tick,
    "Merchant Progress Quests Tick": progress_quests_tick,
}

MERCENARY_TICK_FUNCTIONS = {
    "Mercenary Upkeep Tick": mercenary_upkeep_tick,
    "Mercenary Progress Quests Tick": progress_quests_tick,
}

FACTION_TICK_FUNCTIONS = {
    "Faction Income Tick": faction_income_tick,
    "Faction Progress Quests Tick": progress_quests_tick,
}

MARKET_TICK_FUNCTIONS = {
    "Market Income Tick": market_income_tick,
}

VASSAL_SPECIFIC_NATION_TICK_FUNCTIONS = [
    "Nation Concessions Tick",
    "Nation Rebellion Tick",
    "Nation Vassal Compliance Decay Tick",
    "Nation Enclave Compliance Tick",
]

NATION_TICK_FUNCTIONS = {
    "Nation Isolated Diplo Stance Tick": isolated_diplo_stance_tick,
    "Nation Income Tick": nation_income_tick,
    "Nation Tech Tick": nation_tech_tick,
    "Nation Update Rolling Karma Tick": update_rolling_karma,
    "Nation Infamy Decay Tick": nation_infamy_decay_tick,
    "Nation Prestige Gain Tick": nation_prestige_gain_tick,
    "Nation Civil War Tick": nation_civil_war_tick,
    "Nation Stability Tick": nation_stability_tick,
    "Nation Concessions Tick": nation_concessions_tick,
    "Nation Rebellion Tick": nation_rebellion_tick,
    "Nation Vassal Compliance Decay Tick": nation_vassal_compliance_decay_tick,
    "Nation Enclave Compliance Tick": nation_enclave_compliance_tick,
    "Nation Passive Expansion Tick": nation_passive_expansion_tick,
    "Nation Modifier Decay Tick": modifier_decay_tick,
    "Nation Progress Quests Tick": progress_quests_tick,
    "Nation Job Cleanup Tick": nation_job_cleanup_tick,
    "AI Decision Tick": ai_decision_tick,
    "Nation Vampirism Tick": vampirism_tick,
    "Nation Undead Tick": undead_tick,
    "Nation Pop Loss Tick": pop_loss_tick,
    "Nation Pop Flee Tick": pop_flee_tick,
    "Nation Temperament Tick": temperament_tick,
    "District Duration Tick": district_duration_tick,
    "Empire Prestige Decay Tick": empire_prestige_decay_tick,
}

def ongoing_trade_route_tick(_old_nations, _new_nations, _schema):
    """Lifecycle-only tick: ends routes that have passed their last delivery session."""
    current_session = _tr_current_session()
    log = run_trade_route_lifecycle(current_session)
    return (log + "\n") if log else ""


NATION_CROSS_TICK_FUNCTIONS = {
    "Ongoing Trade Route Tick": ongoing_trade_route_tick,
    "AI Market Matching Tick": ai_market_matching_tick,
    "Market Price Tick": market_price_tick,
}

def generate_all_ai_rulers_tick():
    """Generate AI rulers for all nations and mercenary companies without a living ruler or direct players."""
    result = ""
    character_schema, _ = get_data_on_category("characters")

    living_ruler_org_ids = {
        str(c["ruling_nation_org"])
        for c in mongo.db.characters.find(
            {"ruling_nation_org": {"$ne": None}, "health_status": {"$ne": "Dead"}},
            {"ruling_nation_org": 1},
        )
        if c.get("ruling_nation_org")
    }

    for collection_name in ("nations", "mercenaries"):
        try:
            org_schema, org_db = get_data_on_category(collection_name)
        except Exception:
            continue
        for org in org_db.find():
            if str(org["_id"]) not in living_ruler_org_ids and not org.get("players"):
                result += generate_ai_character(org, org_schema, character_schema)

    return result


ERA_GENERAL_TICK_FUNCTIONS = {
    "Backup Database": None,   # handled directly in era_tick() before nation processing
    "Era Give Tick Summary": None,  # handled directly in era_tick() after all processing
    "Era Relations Decay to Neutral": era_relations_decay_tick,
    "Era Pop Growth (All Nations)": era_pop_growth_tick,
    "Age Pop Growth (Skip Infertile Races)": age_pop_growth_tick,
    "Era Artifact Loss": era_artifact_loss_tick,
    "Era Character Aging": era_character_aging_tick,
    "Era Generate AI Rulers": generate_all_ai_rulers_tick,
}

ERA_NATION_TICK_FUNCTIONS = {
    "Era Nation Reset Rolling Karma to Zero": reset_rolling_karma_to_zero,
    "Era Nation Reset All Temperaments": reset_all_temperaments,
    "Era Reset Stability to Balanced": era_reset_stability_to_balanced_tick,
    "Era Compliance Decay to Neutral": era_compliance_decay_tick,
    "Era Resource Stockpile Decay": era_resource_stockpile_decay_tick,
    "Era Formal Storage Bonus": era_formal_storage_bonus_tick,
    "Era AI Resource Grant": era_ai_resource_grant_tick,
    "Age Nation Tech Cost Reduction Tick": nation_tech_cost_reduction_tick,
}


def era_character_magic_decay_tick(old_character, new_character, schema):
    if old_character.get("health_status", "Healthy") == "Dead":
        return ""
    magic_points = old_character.get("magic_points", 0)
    if not magic_points:
        return ""
    kept_pct = random.uniform(0.4, 0.6)
    new_character["magic_points"] = round(magic_points * kept_pct)
    kept_display = round(kept_pct * 100)
    return f"{old_character.get('name', 'Unknown')} kept ~{kept_display}% of magic stockpile ({magic_points} → {new_character['magic_points']}).\n"


ERA_CHARACTER_TICK_FUNCTIONS = {
    "Era Character Magic Stockpile Decay": era_character_magic_decay_tick,
}


def era_tick(form_data):
    full_tick_summary = ""

    if "run_Backup Database" in form_data:
        success, message = backup_database()
        if not success:
            return message

    collect_nation_data = any(
        f"run_{label}" in form_data for label in ERA_NATION_TICK_FUNCTIONS
    )

    if collect_nation_data:
        nation_schema, nation_db = get_data_on_category("nations")
        old_nations = list(nation_db.find().sort("name", ASCENDING))
        new_nations = []
        for nation in old_nations:
            if nation:
                nation.update(calculate_all_fields(nation, nation_schema, "nation"))
                new_nations.append(deepcopy(nation))

        for label, fn in ERA_NATION_TICK_FUNCTIONS.items():
            if f"run_{label}" in form_data:
                print(label)
                for i in range(len(old_nations)):
                    result = fn(old_nations[i], new_nations[i], nation_schema)
                    full_tick_summary += result

        for i in range(len(old_nations)):
            change_id = system_request_change(
                data_type="nations",
                item_id=old_nations[i]["_id"],
                change_type="Update",
                before_data=old_nations[i],
                after_data=new_nations[i],
                reason="Era Tick Update for " + old_nations[i]["name"],
            )
            system_approve_change(change_id)

    collect_character_data = any(
        f"run_{label}" in form_data for label in ERA_CHARACTER_TICK_FUNCTIONS
    )

    if collect_character_data:
        character_schema, character_db = get_data_on_category("characters")
        old_characters = list(character_db.find().sort("name", ASCENDING))
        new_characters = []
        for character in old_characters:
            if character:
                character.update(calculate_all_fields(character, character_schema, "character"))
                new_characters.append(deepcopy(character))

        for label, fn in ERA_CHARACTER_TICK_FUNCTIONS.items():
            if f"run_{label}" in form_data:
                print(label)
                for i in range(len(old_characters)):
                    result = fn(old_characters[i], new_characters[i], character_schema)
                    full_tick_summary += result

        for i in range(len(old_characters)):
            change_id = system_request_change(
                data_type="characters",
                item_id=old_characters[i]["_id"],
                change_type="Update",
                before_data=old_characters[i],
                after_data=new_characters[i],
                reason="Era Tick Update for " + old_characters[i].get("name", str(old_characters[i]["_id"])),
            )
            system_approve_change(change_id)

    for label, fn in ERA_GENERAL_TICK_FUNCTIONS.items():
        if fn is None:
            continue  # handled as a special case above (e.g. Backup Database)
        if f"run_{label}" in form_data:
            print(label)
            full_tick_summary += fn()

    if "run_Era Give Tick Summary" in form_data:
        give_tick_summary(full_tick_summary, full_tick_summary)

    return full_tick_summary


def run_era_tick_async(form_data):
    from threading import Thread
    thread = Thread(target=era_tick, args=(form_data,))
    thread.daemon = True
    thread.start()
    return "Era tick started in background."


def adjust_stability(old_nation, new_nation, schema, amounts=[-1], reasons=[""]):
    result = ""
    stability_enum = schema["properties"]["stability"]["enum"]
    stability_index = stability_enum.index(old_nation.get("stability", "Balanced"))
    for i in range(min(len(amounts), len(reasons))):
        stability_index += amounts[i]
        gain_or_loss = "gained" if amounts[i] > 0 else "lost"
        result += f"{old_nation.get('name', 'Unknown')} has {gain_or_loss} {abs(amounts[i])} level(s) of stability due to {reasons[i]}.\n"
    if stability_index < 0:
        civil_war_chance = 0.5
        civil_war_roll = random.random()
        worst_reason = reasons[amounts.index(min(amounts))]
        new_nation[worst_reason + "_civil_war_roll"] = civil_war_roll
        new_nation[worst_reason + "civil_war_chance_at_tick"] = civil_war_chance
        if civil_war_roll <= civil_war_chance:
            stability_index = 1
            result += f"{old_nation.get('name', 'Unknown')} has experienced a civil war due to negative stability from {worst_reason}.\n"
        else:
            stability_index = 0
    elif stability_index >= len(stability_enum):
        stability_index = len(stability_enum) - 1
    
    new_nation["stability"] = stability_enum[stability_index]
    return result
