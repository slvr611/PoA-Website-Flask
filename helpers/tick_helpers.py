from helpers.data_helpers import get_data_on_category
from calculations.field_calculations import calculate_all_fields
from pymongo import ASCENDING
from helpers.change_helpers import request_change, approve_change
from app_core import mongo, json_data
from flask import flash
from app_core import backup_mongodb
import random

def tick(form_data):
    success, message = backup_database()
    if not success:
        return message
    tick_summary = ""

    character_schema, character_db = get_data_on_category("characters")
    old_characters = list(character_db.find().sort("name", ASCENDING))
    new_characters = old_characters.deepcopy()

    for i in range(len(old_characters)):
        calculated_fields = calculate_all_fields(old_characters[i], character_schema, "character")
        old_characters[i].update(calculated_fields)

    for tick_function_label, tick_function in CHARACTER_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            for i in range(len(old_characters)):
                tick_summary += tick_function(old_characters[i], new_characters[i], character_schema)
    
    for i in range(len(old_characters)):
        change_id = request_change(
            data_type="characters",
            item_id=old_characters[i]["_id"],
            change_type="Update",
            before_data=old_characters[i],
            after_data=new_characters[i],
            reason="Tick Update for " + old_characters[i]["name"]
        )
        approve_change(change_id)
    
    merchant_schema, merchant_db = get_data_on_category("characters")
    old_merchants = list(merchant_db.find().sort("name", ASCENDING))
    new_merchants = old_merchants.deepcopy()

    for i in range(len(old_merchants)):
        calculated_fields = calculate_all_fields(old_merchants[i], merchant_schema, "merchant")
        old_merchants[i].update(calculated_fields)

    for tick_function_label, tick_function in MERCHANT_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            for i in range(len(old_merchants)):
                tick_summary += tick_function(old_merchants[i], new_merchants[i], merchant_schema)
    
    for i in range(len(old_merchants)):
        change_id = request_change(
            data_type="merchants",
            item_id=old_merchants[i]["_id"],
            change_type="Update",
            before_data=old_merchants[i],
            after_data=new_merchants[i],
            reason="Tick Update for " + old_merchants[i]["name"]
        )
        approve_change(change_id)
    
    mercenary_schema, mercenary_db = get_data_on_category("characters")
    old_mercenaries = list(mercenary_db.find().sort("name", ASCENDING))
    new_mercenaries = old_mercenaries.deepcopy()

    for i in range(len(old_mercenaries)):
        calculated_fields = calculate_all_fields(old_mercenaries[i], mercenary_schema, "mercenary")
        old_mercenaries[i].update(calculated_fields)

    for tick_function_label, tick_function in MERCENARY_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            for i in range(len(old_mercenaries)):
                tick_summary += tick_function(old_mercenaries[i], new_mercenaries[i], mercenary_schema)
    
    for i in range(len(old_mercenaries)):
        change_id = request_change(
            data_type="mercenaries",
            item_id=old_mercenaries[i]["_id"],
            change_type="Update",
            before_data=old_mercenaries[i],
            after_data=new_mercenaries[i],
            reason="Tick Update for " + old_mercenaries[i]["name"]
        )
        approve_change(change_id)
    
    nation_schema, nation_db = get_data_on_category("nations")
    old_nations = list(nation_db.find().sort("name", ASCENDING))
    new_nations = old_nations.deepcopy()
    
    for i in range(len(old_nations)):
        calculated_fields = calculate_all_fields(old_nations[i], nation_schema, "nation")
        old_nations[i].update(calculated_fields)

    for tick_function_label, tick_function in NATION_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            for i in range(len(old_nations)):
                tick_summary += tick_function(old_nations[i], new_nations[i], nation_schema)
    
    for i in range(len(old_nations)):
        change_id = request_change(
            data_type="nations",
            item_id=old_nations[i]["_id"],
            change_type="Update",
            before_data=old_nations[i],
            after_data=new_nations[i],
            reason="Tick Update for " + old_nations[i]["name"]
        )
        approve_change(change_id)

    if "run_Give Tick Summary" in form_data:
        give_tick_summary(tick_summary)

    return tick_summary

###########################################################
# General Tick Functions
###########################################################

def backup_database():
    success, message = backup_mongodb()
    if not success:
        flash(f"Database backup failed: {message}", "error")
    else:
        flash(f"Database backup successful: {message}", "success")
    return success, message

def give_tick_summary(tick_summary):
    flash(tick_summary)
    return

###########################################################
# Character Tick Functions
###########################################################

def character_death_tick(old_character, new_character, schema):
    result = ""
    if new_character["health_status"] == "Dead":
        return
    death_roll = random.random()
    new_character["death_roll"] = death_roll
    new_character["death_chance_at_tick"] = old_character.get("death_chance", 0)
    if death_roll <= old_character.get("death_chance", 0):
        new_character["health_status"] = "Dead"
        result = f"{old_character['name']} has died.\n"

        if old_character["ruling_nation_org"] != "":
            nation_schema, nation_db = get_data_on_category("nations")
            old_nation = nation_db.find_one({"_id": old_character["ruling_nation_org"]})
            new_nation = old_nation.copy()
            calculated_fields = calculate_all_fields(old_nation, nation_schema, "nation")
            old_nation.update(calculated_fields)
            if old_nation:
                leader_death_stab_loss_roll = random.random()
                new_nation["leader_death_stab_loss_roll"] = leader_death_stab_loss_roll
                new_nation["leader_death_stab_loss_chance_at_tick"] = old_nation.get("stability_loss_chance_on_leader_death", 0)
                if leader_death_stab_loss_roll <= old_nation.get("stability_loss_chance_on_leader_death", 0):
                    result += f"{old_nation['name']} has lost stability due to the death of their leader.\n"
                    stability_enum = nation_schema["properties"]["stability"]["enum"]
                    stability_index = stability_enum.find(old_nation["stability"])
                    stability_index = max(stability_index - 1, 0)
                    new_nation["stability"] = stability_enum[stability_index]
                    # TODO: Add code to account for Autocracy increased stab loss on leader death based on age
                    change_id = request_change(
                        data_type="nations",
                        item_id=old_nation["_id"],
                        change_type="Update",
                        before_data=old_nation,
                        after_data=new_nation,
                        reason="Death of " + old_character["name"] + " has caused a stability loss for " + old_nation["name"]
                    )
                    approve_change(change_id)
        
        artifact_schema, artifact_db = get_data_on_category("artifacts")
        artifacts = list(artifact_db.find({"_id": old_character["ruling_artifact"]}))
        for old_artifact in artifacts:
            new_artifact = old_artifact.copy()
            calculated_fields = calculate_all_fields(old_artifact, artifact_schema, "artifact")
            old_artifact.update(calculated_fields)

            loss_roll = random.random()
            new_artifact["owner_death_loss_roll"] = loss_roll
            new_artifact["owner_death_loss_chance_at_tick"] = old_artifact.get("owner_death_loss_chance", 0)
            if loss_roll <= old_artifact.get("owner_death_loss_chance", 0):
                new_artifact["owner"] = "Lost"
                change_id = request_change(
                    data_type="artifacts",
                    item_id=old_artifact["_id"],
                    change_type="Update",
                    before_data=old_artifact,
                    after_data=new_artifact,
                    reason="Death of " + old_character["name"] + " has caused " + old_artifact["name"] + " to be lost"
                )
                approve_change(change_id)

    return result

def character_heal_tick(old_character, new_character, schema):
    result = ""
    if new_character["health_status"] == "Healthy":
        return
    if new_character["health_status"] == "Dead":
        return
    
    health_status_enum = schema["properties"]["health_status"]["enum"]
    health_index = health_status_enum.find(old_character["health_status"])

    heal_roll = random.random()
    new_character["heal_roll"] = heal_roll
    new_character["heal_chance_at_tick"] = old_character.get("heal_chance", 0)
    if heal_roll <= old_character.get("heal_chance", 0):
        health_index = max(health_index - 1, 0)
        new_character["health_status"] = health_status_enum[health_index]
        result = f"{old_character['name']} has healed from {old_character['health_status']} to {new_character['health_status']}.\n"
    return result

def character_mana_tick(old_character, new_character, schema):
    new_character["current_magic_points"] = min(old_character["current_magic_points"] + old_character.get("magic_point_income", 0), old_character.get("magic_point_capacity", 0))
    return

def character_age_tick(old_character, new_character, schema):
    new_character["age"] = old_character["age"] + 1
    return

###########################################################
# Artifact Tick Functions
###########################################################

def artifact_loss_tick(old_artifact, new_artifact, schema):
    result = ""
    if old_artifact["owner"] == "Lost":
        return
    loss_roll = random.random()
    new_artifact["loss_roll"] = loss_roll
    new_artifact["loss_chance_at_tick"] = old_artifact.get("passive_loss_chance", 0)
    if loss_roll <= old_artifact.get("passive_loss_chance", 0):
        new_artifact["owner"] = "Lost"
        result = f"{old_artifact['name']} has been lost.\n"
    return result

###########################################################
# Merchant Tick Functions
###########################################################

def merchant_income_tick(old_merchant, new_merchant, schema):
    new_merchant["treasury"] = old_merchant["treasury"] + old_merchant.get("income", 0)

    for resource, amount in old_merchant.get("resource_income", {}).items():
        new_merchant[resource] = old_merchant[resource] + amount
    return

###########################################################
# Mercenary Tick Functions
###########################################################

def mercenary_upkeep_tick(old_mercenary, new_mercenary, schema):
    new_mercenary["treasury"] = old_mercenary["treasury"] - old_mercenary.get("upkeep", 0)
    return

###########################################################
# Nation Tick Functions
###########################################################

def nation_income_tick(old_nation, new_nation, schema):
    new_nation["money"] = old_nation["money"] + old_nation.get("money_income", 0)
    for resource, amount in old_nation.get("resource_income", {}).items():
        new_nation[resource] = old_nation[resource] + amount

    return

def update_rolling_karma(old_nation, new_nation, schema):
    event_type = old_nation.get("event_type", "Unknown")
    if event_type in ["Horrendous", "Abysmal", "Very Bad", "Bad"]:
        new_nation["rolling_karma"] = int(old_nation.get("rolling_karma", 0)) + 1
    elif event_type in ["Good", "Very Good", "Fantastic", "Wonderous"]:
        new_nation["rolling_karma"] = int(old_nation.get("rolling_karma", 0)) - 1

    return

def nation_infamy_decay_tick(old_nation, new_nation, schema):
    # TODO: Prevent Decay while at war
    if old_nation["infamy"] < 5:
        new_nation["infamy"] = 0
        return
    else:
        new_nation["infamy"] = int(round(old_nation["infamy"] / 10)) * 5
    return

def nation_prestige_gain_tick(old_nation, new_nation, schema):
    new_nation["prestige"] = old_nation["prestige"] + old_nation.get("prestige_gain", 0)
    return

def nation_stability_tick(old_nation, new_nation, schema):
    result = ""
    stability_enum = schema["properties"]["stability"]["enum"]
    stability_index = stability_enum.find(old_nation["stability"])

    stability_gain_roll = random.random()
    new_nation["stability_gain_roll"] = stability_gain_roll
    new_nation["stability_gain_chance_at_tick"] = old_nation.get("stability_gain_chance", 0)
    if stability_gain_roll <= old_nation.get("stability_gain_chance", 0):
        stability_index = min(stability_index + 1, len(stability_enum) - 1)
        result += f"{old_nation['name']} has gained stability.\n"

    stability_loss_roll = random.random()
    new_nation["stability_loss_roll"] = stability_loss_roll
    new_nation["stability_loss_chance_at_tick"] = old_nation.get("stability_loss_chance", 0)
    if stability_loss_roll <= old_nation.get("stability_loss_chance", 0):
        stability_index = max(stability_index - 1, 0)
        result += f"{old_nation['name']} has lost stability.\n"

    new_nation["stability"] = stability_enum[stability_index]

    return result

def nation_concessions_tick(old_nation, new_nation, schema):
    if old_nation["overlord"] == "":
        return

    result = ""
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
        first_amount = random.randint(1, concessions_qty)
        second_amount = concessions_qty - first_amount

        new_nation["concessions"] = {
            first_resource: first_amount,
            second_resource: second_amount
        }

        result += f"{old_nation['name']} has demanded concessions from their overlord.\n"
    return result

def nation_rebellion_tick(old_nation, new_nation, schema):
    if old_nation["overlord"] == "":
        return
    result = ""
    rebellion_roll = random.random()
    new_nation["rebellion_roll"] = rebellion_roll
    new_nation["rebellion_chance_at_tick"] = old_nation.get("rebellion_chance", 0)
    if rebellion_roll <= old_nation.get("rebellion_chance", 0):
        result += f"{old_nation['name']} has rebelled against their overlord.\n"

    return result

def nation_passive_expansion_tick(old_nation, new_nation, schema):
    result = ""
    expansion_roll = random.random()
    new_nation["expansion_roll"] = expansion_roll
    new_nation["expansion_chance_at_tick"] = old_nation.get("passive_expansion_chance", 0)
    if expansion_roll <= old_nation.get("passive_expansion_chance", 0):
        result += f"{old_nation['name']} has expanded into adjacent territory.\n"
    return result

def nation_modifier_decay_tick(old_nation, new_nation, schema):
    new_modifiers = []
    for modifier in old_nation["modifiers"]:
        if modifier["duration"] > 0:
            modifier["duration"] -= 1
        if modifier["duration"] != 0:
            new_modifiers.append(modifier)
    new_nation["modifiers"] = new_modifiers
    return

def nation_job_cleanup_tick(old_nation, new_nation, schema):
    new_jobs = {}
    for job in old_nation["jobs"].keys():
        new_jobs[job] = 0
    new_nation["jobs"] = new_jobs
    return

def reset_rolling_karma_to_zero(old_nation, new_nation, schema):
    new_nation["rolling_karma"] = 0

    return

###########################################################
# Tick Function Constants
###########################################################

GENERAL_TICK_FUNCTIONS = {
    "Backup Database": backup_database,
    "Give Tick Summary": give_tick_summary,
}

CHARACTER_TICK_FUNCTIONS = {
    "Character Death Tick": character_death_tick,
    "Character Heal Tick": character_heal_tick,
    "Character Mana Tick": character_mana_tick,
    "Character Age Tick": character_age_tick,
}

ARTIFACT_TICK_FUNCTIONS = {
    "Artifact Loss Tick": artifact_loss_tick,
}

MERCHANT_TICK_FUNCTIONS = {
    "Merchant Income Tick": merchant_income_tick,
}

MERCENARY_TICK_FUNCTIONS = {
    "Mercenary Upkeep Tick": mercenary_upkeep_tick,
}

NATION_TICK_FUNCTIONS = {
    "Nation Income Tick": nation_income_tick,
    "Nation Update Rolling Karma Tick": update_rolling_karma,
    "Nation Infamy Decay Tick": nation_infamy_decay_tick,
    "Nation Prestige Gain Tick": nation_prestige_gain_tick,
    "Nation Stability Tick": nation_stability_tick,
    "Nation Concessions Tick": nation_concessions_tick,
    "Nation Rebellion Tick": nation_rebellion_tick,
    "Nation Passive Expansion Tick": nation_passive_expansion_tick,
    "Nation Modifier Decay Tick": nation_modifier_decay_tick,
    "Nation Job Cleanup Tick": nation_job_cleanup_tick,
    "Nation Reset Rolling Karma to Zero (Generally Don't Use)": reset_rolling_karma_to_zero,
}
