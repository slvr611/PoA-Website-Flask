from bson import ObjectId
from helpers.data_helpers import get_data_on_category
from calculations.field_calculations import calculate_all_fields
from pymongo import ASCENDING
from helpers.change_helpers import system_request_change, system_approve_change
from app_core import mongo, json_data, upload_to_s3, character_stats
from flask import flash
from app_core import backup_mongodb, category_data, temperament_enum, base_temperament_odds, cultural_trait_temperament_modifiers
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
                    if old_characters[i].get("player", "None") != "None":
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
                            if character.get("player", "None") != "None":
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
                            if character.get("player", "None") != "None":
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
                            if character.get("player", "None") != "None":
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
                            if character.get("player", "None") != "None":
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
    for tick_function_label, tick_function in NATION_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
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
                    rulers = old_nations[i].get("rulers", [])
                    for ruler in rulers:
                        try:
                            character = character_db.find_one({"_id": ObjectId(ruler)})
                            if character.get("player", "None") != "None":
                                player_tick_summary += result
                                break
                        except:
                            pass
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
    success, message = backup_mongodb()
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
    for modifier in old_target.get("modifiers", []):
        new_modifier = deepcopy(modifier)
        if int(new_modifier["duration"]) > 0:
            new_modifier["duration"] = int(new_modifier["duration"]) - 1
        if int(new_modifier["duration"]) != 0:
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
    if old_character.get("stat_gain_chance", 0) <= 0 or old_character.get("health_status", "Healthy") == "Dead":
        return ""
    stat_gain_roll = random.random()
    new_character["stat_gain_roll"] = stat_gain_roll
    new_character["stat_gain_chance_at_tick"] = old_character.get("stat_gain_chance", 0)
    if stat_gain_roll <= old_character.get("stat_gain_chance", 0):
        possible_stats = []
        for stat in character_stats:
            if old_character.get(stat, 0) < old_character.get(stat + "_cap", 6):
                possible_stats.append(stat)
        if possible_stats and len(possible_stats) > 0:
            stat = random.choice(possible_stats)
            modifiers = new_character.get("modifiers", [])
            modifiers.append({"field": stat, "value": 1, "duration": -1, "source": "Stat gain tick"})
            new_character["modifiers"] = modifiers
            result = f"{old_character.get('name', 'Unknown')} has gained a level of {stat}.\n"
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
            lost_artifact = random.choice(unequipped_artifacts)
            lost_artifact["owner"] = "Lost"
            result = f"{old_character.get('name', 'Unknown')} has lost {lost_artifact.get('name', 'Unknown')}.\n"
            change_id = system_request_change(
                data_type="artifacts",
                item_id=lost_artifact["_id"],
                change_type="Update",
                before_data=lost_artifact,
                after_data=lost_artifact,
                reason="Loss of " + old_character.get('name', 'Unknown') + " has caused " + lost_artifact.get('name', 'Unknown') + " to be lost"
            )
            system_approve_change(change_id)
        else:
            equipped_artifacts = list(artifact_db.find({"owner": str(old_character.get("_id", "")), "equipped": True}))
            if equipped_artifacts:
                lost_artifact = random.choice(equipped_artifacts)
                lost_artifact["owner"] = "Lost"
                result = f"{old_character.get('name', 'Unknown')} has lost {lost_artifact.get('name', 'Unknown')}.\n"
                change_id = system_request_change(
                    data_type="artifacts",
                    item_id=lost_artifact["_id"],
                    change_type="Update",
                    before_data=lost_artifact,
                    after_data=lost_artifact,
                    reason="Loss of " + old_character.get('name', 'Unknown') + " has caused " + lost_artifact.get('name', 'Unknown') + " to be lost"
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
                modifier["value"] = min(modifier["value"] + gain_rate, cap)
                new_nation["modifiers"] = modifiers
                return f"{old_nation.get('name', 'Unknown')} has had the stability gain chance modifier from their Isolated diplomatic stance increased by 0.1.\n"
        modifiers.append({"field": "stability_gain_chance", "value": max(gain_rate, cap), "duration": -1, "source": "Isolated Diplomatic Stance"})
        new_nation["modifiers"] = modifiers
        return f"{old_nation.get('name', 'Unknown')} has had the stability gain chance modifier from their Isolated diplomatic stance increased by 0.1.\n"

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
    new_nation["resource_storage"] = {}
    new_nation["production_at_tick"] = old_nation.get("resource_excess", {})
    for resource, amount in old_nation.get("resource_excess", {}).items():
        new_nation["resource_storage"][resource] = min(old_nation.get("resource_storage", {}).get(resource, 0) + amount, old_nation.get("nation_resource_capacity", {}).get(resource, 0))
    
    return ""

def nation_tech_tick(old_nation, new_nation, schema):
    new_nation["research_production_at_tick"] = old_nation.get("resource_production", {}).get("research", 0)
    new_nation["research_consumption_at_tick"] = old_nation.get("resource_consumption", {}).get("research", 0)
    json_tech_data = json_data["tech"]

    new_nation["technologies"] = deepcopy(old_nation.get("technologies", {}))
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
    # TODO: Prevent Decay while at war
    if int(old_nation.get("infamy", 0)) < 5:
        new_nation["infamy"] = 0
        return ""
    else:
        new_nation["infamy"] = int(round(old_nation.get("infamy", 0) / 10)) * 5
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

    if stability_gain_roll <= old_nation.get("stability_gain_chance", 0):
        stability_gained = 1

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

def nation_passive_expansion_tick(old_nation, new_nation, schema):
    result = ""
    expansion_roll = random.random()
    new_nation["expansion_roll"] = expansion_roll
    new_nation["expansion_chance_at_tick"] = old_nation.get("passive_expansion_chance", 0)
    if expansion_roll <= old_nation.get("passive_expansion_chance", 0):
        result += f"{old_nation.get('name', 'Unknown')} has expanded into adjacent territory.\n"
    return result

def nation_job_cleanup_tick(old_nation, new_nation, schema):
    new_jobs = {}
    for job in old_nation.get("jobs", {}).keys():
        if job != "vampire":
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
    
    for tech, value in new_nation["technologies"].items():
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

def library_tick(old_nation, new_nation, schema):
    districts = new_nation.get("districts", [])
    for district in districts:
        if "library" in district.get("type", ""):
            required_turns = old_nation.get("research_production_per_turns_with_library", 0)
            max_research = old_nation.get("max_research_production_per_turns_with_library", 0)
            modifiers = new_nation.get("modifiers", [])
            for modifier in modifiers:
                if modifier.get("field", "") == "Turns with Library" and modifier.get("source", "") == "Library District":
                    modifier["value"] += 1
                    if modifier["value"] % required_turns == 0:
                        for modifier_2 in modifiers:
                            if modifier_2.get("field", "") == "research_production" and modifier_2.get("source", "") == "Library District":
                                modifier_2["value"] = min(modifier["value"] / required_turns, max_research)
                                new_nation["modifiers"] = modifiers
                                return f"{old_nation.get('name', 'Unknown')} has had their research production increased by 1 because the library district has been active for {modifier['value']} turns.\n" and modifier_2.get("source", "") == "Library District"
                            
                        modifiers.append({"field": "research_production", "value": 1, "duration": -1, "source": "Library District"})
                        new_nation["modifiers"] = modifiers
                        return f"{old_nation.get('name', 'Unknown')} has had their research production increased by 1 because the library district has been active for {modifier['value']} turns.\n"
                    
                    new_nation["modifiers"] = modifiers
                    return f"{old_nation.get('name', 'Unknown')} has had their Turns with Library modifier increased by 1.\n"
                
            modifiers.append({"field": "Turns with Library", "value": 1, "duration": -1, "source": "Library District"})
            new_nation["modifiers"] = modifiers
            return f"{old_nation.get('name', 'Unknown')} has had their Turns with Library modifier increased by 1.\n"

    return ""

###########################################################
# Tick Function Constants
###########################################################

GENERAL_TICK_FUNCTIONS = {
    "Backup Database": backup_database,
    "Give Tick Summary": give_tick_summary,
    "Tick Session Number": tick_session_number,
}

CHARACTER_TICK_FUNCTIONS = {
    "Character Death Tick": character_death_tick,
    "Character Heal Tick": character_heal_tick,
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

NATION_TICK_FUNCTIONS = {
    "Nation Isolated Diplo Stance Tick": isolated_diplo_stance_tick,
    "AI Resource Desire Tick": ai_resource_desire_tick,
    "Nation Income Tick": nation_income_tick,
    "Nation Tech Tick": nation_tech_tick,
    "Nation Update Rolling Karma Tick": update_rolling_karma,
    "Nation Infamy Decay Tick": nation_infamy_decay_tick,
    "Nation Prestige Gain Tick": nation_prestige_gain_tick,
    "Nation Civil War Tick": nation_civil_war_tick,
    "Nation Stability Tick": nation_stability_tick,
    "Nation Concessions Tick": nation_concessions_tick,
    "Nation Rebellion Tick": nation_rebellion_tick,
    "Nation Passive Expansion Tick": nation_passive_expansion_tick,
    "Nation Modifier Decay Tick": modifier_decay_tick,
    "Nation Progress Quests Tick": progress_quests_tick,
    "Nation Job Cleanup Tick": nation_job_cleanup_tick,
    "Nation Vampirism Tick": vampirism_tick,
    "Nation Undead Tick": undead_tick,
    "Nation Pop Loss Tick": pop_loss_tick,
    "Nation Temperament Tick": temperament_tick,
    "Nation Library Tick": library_tick,
    "Nation Tech Cost Reduction Tick (Generally Don't Use)": nation_tech_cost_reduction_tick,
    "Nation Reset Rolling Karma to Zero (Generally Don't Use)": reset_rolling_karma_to_zero,
    "Nation Reset All Temperaments (Generally Don't Use)": reset_all_temperaments,
}

def adjust_stability(old_nation, new_nation, schema, amounts=[-1], reasons=[""]):
    result = ""
    stability_enum = schema["properties"]["stability"]["enum"]
    stability_index = stability_enum.index(old_nation.get("stability", "Balanced"))
    for i in range(min(len(amounts), len(reasons))):
        stability_index += amounts[i]
        result += f"{old_nation.get('name', 'Unknown')} has lost {amounts[i]} level(s) of stability due to {reasons[i]}.\n"
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