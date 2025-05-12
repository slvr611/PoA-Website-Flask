from helpers.data_helpers import get_data_on_category
from calculations.field_calculations import calculate_all_fields
from pymongo import ASCENDING
from helpers.change_helpers import system_request_change, system_approve_change
from app_core import mongo, json_data, upload_to_s3
from flask import flash
from app_core import backup_mongodb
import random
import os
import datetime

def tick(form_data):
    if "run_Backup Database" in form_data:
        success, message = backup_database()
        if not success:
            return message
    tick_summary = ""



    character_schema, character_db = get_data_on_category("characters")
    old_characters = list(character_db.find().sort("name", ASCENDING))
    new_characters = []
    calculated_characters = []
    for character in old_characters:
        if character:
            new_characters.append(character.copy())
            calculated_characters.append(character.copy())
    
    for character in calculated_characters:
        calculated_fields = calculate_all_fields(character, character_schema, "character")
        character.update(calculated_fields)
    
    for tick_function_label, tick_function in CHARACTER_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            print(tick_function_label)
            for i in range(len(old_characters)):
                tick_summary += tick_function(old_characters[i], new_characters[i], calculated_characters[i], character_schema)


        
    artifact_schema, artifact_db = get_data_on_category("artifacts")
    old_artifacts = list(artifact_db.find().sort("name", ASCENDING))
    new_artifacts = []
    calculated_artifacts = []
    for artifact in old_artifacts:
        if artifact:
            new_artifacts.append(artifact.copy())
            calculated_artifacts.append(artifact.copy())
    
    for artifact in calculated_artifacts:
        calculated_fields = calculate_all_fields(artifact, artifact_schema, "artifact")
        artifact.update(calculated_fields)

    for tick_function_label, tick_function in ARTIFACT_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            print(tick_function_label)
            for i in range(len(old_artifacts)):
                tick_summary += tick_function(old_artifacts[i], new_artifacts[i], calculated_artifacts[i], artifact_schema)



    merchant_schema, merchant_db = get_data_on_category("merchants")
    old_merchants = list(merchant_db.find().sort("name", ASCENDING))
    new_merchants = []
    calculated_merchants = []
    for merchant in old_merchants:
        if merchant:
            new_merchants.append(merchant.copy())
            calculated_merchants.append(merchant.copy())
    
    for merchant in calculated_merchants:
        calculated_fields = calculate_all_fields(merchant, merchant_schema, "merchant")
        merchant.update(calculated_fields)

    for tick_function_label, tick_function in MERCHANT_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            print(tick_function_label)
            for i in range(len(old_merchants)):
                tick_summary += tick_function(old_merchants[i], new_merchants[i], calculated_merchants[i], merchant_schema)



    mercenary_schema, mercenary_db = get_data_on_category("mercenaries")
    old_mercenaries = list(mercenary_db.find().sort("name", ASCENDING))
    new_mercenaries = []
    calculated_mercenaries = []
    for mercenary in old_mercenaries:
        if mercenary:
            new_mercenaries.append(mercenary.copy())
            calculated_mercenaries.append(mercenary.copy())
    
    for mercenary in calculated_mercenaries:
        calculated_fields = calculate_all_fields(mercenary, mercenary_schema, "mercenary")
        mercenary.update(calculated_fields)

    for tick_function_label, tick_function in MERCENARY_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            print(tick_function_label)
            for i in range(len(old_mercenaries)):
                tick_summary += tick_function(old_mercenaries[i], new_mercenaries[i], calculated_mercenaries[i], mercenary_schema)



    faction_schema, faction_db = get_data_on_category("factions")
    old_factions = list(faction_db.find().sort("name", ASCENDING))
    new_factions = []
    calculated_factions = []
    for faction in old_factions:
        if faction:
            new_factions.append(faction.copy())
            calculated_factions.append(faction.copy())

    for faction in calculated_factions:
        calculated_fields = calculate_all_fields(faction, faction_schema, "faction")
        faction.update(calculated_fields)

    for tick_function_label, tick_function in FACTION_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            print(tick_function_label)
            for i in range(len(old_nations)):
                tick_summary += tick_function(old_factions[i], new_factions[i], calculated_factions[i], faction_schema)



    nation_schema, nation_db = get_data_on_category("nations")
    old_nations = list(nation_db.find().sort("name", ASCENDING))
    new_nations = []
    calculated_nations = []
    for nation in old_nations:
        if nation:
            new_nations.append(nation.copy())
            calculated_nations.append(nation.copy())
    
    for nation in calculated_nations:
        calculated_fields = calculate_all_fields(nation, nation_schema, "nation")
        nation.update(calculated_fields)

    for tick_function_label, tick_function in NATION_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            print(tick_function_label)
            for i in range(len(old_nations)):
                tick_summary += tick_function(old_nations[i], new_nations[i], calculated_nations[i], nation_schema)


    
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
        give_tick_summary(tick_summary)

    return tick_summary

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

def give_tick_summary(tick_summary):
    """Save tick summary to a file and optionally email it"""
    print(tick_summary)  # Keep console logging
    
    # Create a timestamp for the filename
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # Create summaries directory if it doesn't exist
    summary_dir = os.path.join(os.getcwd(), 'summaries')
    os.makedirs(summary_dir, exist_ok=True)
    
    # Save to file
    summary_filename = f"tick_summary_{timestamp}.txt"
    summary_path = os.path.join(summary_dir, summary_filename)
    
    with open(summary_path, 'w') as f:
        f.write(tick_summary)
    
    
    # If S3 is configured, upload the summary
    if os.getenv("S3_BUCKET_NAME"):
        upload_to_s3(summary_path, f"tick_summaries/{summary_filename}")
    
    return summary_path

###########################################################
# Character Tick Functions
###########################################################

def character_death_tick(old_character, new_character, calculated_character, schema):
    result = ""
    if new_character.get("health_status", "Healthy") == "Dead":
        return ""
    death_roll = random.random()
    new_character["death_roll"] = death_roll
    new_character["death_chance_at_tick"] = calculated_character.get("death_chance", 0)
    if death_roll <= calculated_character.get("death_chance", 0):
        new_character["health_status"] = "Dead"
        result = f"{old_character.get('name', 'Unknown')} has died.\n"

        if old_character.get("ruling_nation_org", "") != "":
            nation_schema, nation_db = get_data_on_category("nations")
            old_nation = nation_db.find_one({"_id": old_character.get("ruling_nation_org", "")})
            if old_nation:
                new_nation = old_nation.copy()
                calculated_nation = old_nation.copy()
                calculated_fields = calculate_all_fields(old_nation, nation_schema, "nation")
                calculated_nation.update(calculated_fields)
                leader_death_stab_loss_roll = random.random()
                new_nation["leader_death_stab_loss_roll"] = leader_death_stab_loss_roll
                new_nation["leader_death_stab_loss_chance_at_tick"] = calculated_nation.get("stability_loss_chance_on_leader_death", 0)
                if leader_death_stab_loss_roll <= calculated_nation.get("stability_loss_chance_on_leader_death", 0):
                    result += f"{old_nation.get('name', 'Unknown')} has lost stability due to the death of their leader.\n"
                    stability_enum = nation_schema["properties"]["stability"]["enum"]
                    stability_index = stability_enum.find(old_nation["stability"])
                    stability_index = max(stability_index - 1, 0)
                    new_nation["stability"] = stability_enum[stability_index]
                    # TODO: Add code to account for Autocracy increased stab loss on leader death based on age
                    change_id = system_request_change(
                        data_type="nations",
                        item_id=old_nation["_id"],
                        change_type="Update",
                        before_data=old_nation,
                        after_data=new_nation,
                        reason="Death of " + old_character.get('name', 'Unknown') + " has caused a stability loss for " + old_nation.get('name', 'Unknown')
                    )
                    system_approve_change(change_id)
        
        artifact_schema, artifact_db = get_data_on_category("artifacts")
        artifacts = list(artifact_db.find({"owner": old_character["_id"]}))
        for old_artifact in artifacts:
            if old_artifact:
                new_artifact = old_artifact.copy()
                calculated_artifact = old_artifact.copy()
                calculated_fields = calculate_all_fields(old_artifact, artifact_schema, "artifact")
                calculated_artifact.update(calculated_fields)

                loss_roll = random.random()
                new_artifact["owner_death_loss_roll"] = loss_roll
                new_artifact["owner_death_loss_chance_at_tick"] = calculated_artifact.get("owner_death_loss_chance", 0)
                if loss_roll <= calculated_artifact.get("owner_death_loss_chance", 0):
                    new_artifact["owner"] = "Lost"
                    change_id = system_request_change(
                        data_type="artifacts",
                        item_id=old_artifact["_id"],
                        change_type="Update",
                        before_data=old_artifact,
                        after_data=new_artifact,
                        reason="Death of " + old_character.get('name', 'Unknown') + " has caused " + old_artifact.get('name', 'Unknown') + " to be lost"
                    )
                    system_approve_change(change_id)

    return result

def character_heal_tick(old_character, new_character, calculated_character, schema):
    result = ""
    if new_character["health_status"] == "Healthy":
        return ""
    if new_character["health_status"] == "Dead":
        return ""
    
    health_status_enum = schema["properties"]["health_status"]["enum"]
    health_index = health_status_enum.index(old_character["health_status"])

    heal_roll = random.random()
    new_character["heal_roll"] = heal_roll
    new_character["heal_chance_at_tick"] = calculated_character.get("heal_chance", 0)
    if heal_roll <= calculated_character.get("heal_chance", 0):
        health_index = max(health_index - 1, 0)
        new_character["health_status"] = health_status_enum[health_index]
        result = f"{old_character.get('name', 'Unknown')} has healed from {old_character.get('health_status', 'Unknown')} to {new_character.get('health_status', 'Unknown')}.\n"
    return result

def character_mana_tick(old_character, new_character, calculated_character, schema):
    new_character["magic_points"] = min(old_character.get("magic_points", 0) + calculated_character.get("magic_point_income", 0), calculated_character.get("magic_point_capacity", 0))
    return ""

def character_age_tick(old_character, new_character, calculated_character, schema):
    new_character["age"] = old_character["age"] + 1
    return ""

def character_modifier_decay_tick(old_character, new_character, calculated_character, schema):
    new_modifiers = []
    for modifier in old_character.get("modifiers", []):
        new_modifier = modifier.copy()
        if int(new_modifier["duration"]) > 0:
            new_modifier["duration"] = int(new_modifier["duration"]) - 1
        if int(new_modifier["duration"]) != 0:
            new_modifiers.append(new_modifier)
    new_character["modifiers"] = new_modifiers
    return ""

###########################################################
# Artifact Tick Functions
###########################################################

def artifact_loss_tick(old_artifact, new_artifact, calculated_artifact, schema):
    result = ""
    if old_artifact["owner"] == "Lost":
        return ""
    loss_roll = random.random()
    new_artifact["loss_roll"] = loss_roll
    new_artifact["loss_chance_at_tick"] = calculated_artifact.get("passive_loss_chance", 0)
    if loss_roll <= calculated_artifact.get("passive_loss_chance", 0):
        new_artifact["owner"] = "Lost"
        result = f"{old_artifact.get('name', 'Unknown')} has been lost.\n"
    return result

###########################################################
# Merchant Tick Functions
###########################################################

def merchant_income_tick(old_merchant, new_merchant, calculated_merchant, schema):
    new_merchant["treasury"] = int(old_merchant.get("treasury", 0)) + calculated_merchant.get("income", 0)

    new_merchant["resource_storage"] = {}
    for resource, amount in calculated_merchant.get("resource_income", {}).items():
        new_merchant["resource_storage"][resource] = old_merchant["resource_storage"][resource] + amount
    return ""

###########################################################
# Mercenary Tick Functions
###########################################################

def mercenary_upkeep_tick(old_mercenary, new_mercenary, calculated_mercenary, schema):
    new_mercenary["treasury"] = int(old_mercenary.get("treasury", 0)) - calculated_mercenary.get("upkeep", 0)
    return ""

###########################################################
# Faction Tick Functions
###########################################################

def faction_income_tick(old_faction, new_faction, calculated_faction, schema):
    new_faction["influence"] = int(old_faction.get("influence", 0)) + calculated_faction.get("influence_income", 0)
    return ""


###########################################################
# Nation Tick Functions
###########################################################

def nation_income_tick(old_nation, new_nation, calculated_nation, schema):
    new_nation["money"] = int(old_nation.get("money", 0)) + calculated_nation.get("money_income", 0)
    new_nation["resource_storage"] = {}
    for resource, amount in calculated_nation.get("resource_excess", {}).items():
        new_nation["resource_storage"][resource] = min(calculated_nation.get("resource_storage", {}).get(resource, 0) + amount, old_nation.get("resource_capacity", {}).get(resource, 0))
    
    return ""

def update_rolling_karma(old_nation, new_nation, calculated_nation, schema):
    event_type = old_nation.get("event_type", "Unknown")
    if event_type in ["Horrendous", "Abysmal", "Very Bad", "Bad"]:
        new_nation["rolling_karma"] = int(old_nation.get("rolling_karma", 0)) + 1
    elif event_type in ["Good", "Very Good", "Fantastic", "Wonderous"]:
        new_nation["rolling_karma"] = int(old_nation.get("rolling_karma", 0)) - 1

    return ""

def nation_infamy_decay_tick(old_nation, new_nation, calculated_nation, schema):
    # TODO: Prevent Decay while at war
    if int(old_nation.get("infamy", 0)) < 5:
        new_nation["infamy"] = 0
        return ""
    else:
        new_nation["infamy"] = int(round(old_nation.get("infamy", 0) / 10)) * 5
    return ""

def nation_prestige_gain_tick(old_nation, new_nation, calculated_nation, schema):
    if not old_nation.get("empire", False):
        return ""
    old_nation_prestige = old_nation.get("prestige", 0)
    if old_nation_prestige == "":
        old_nation_prestige = 0
    new_nation["prestige"] = old_nation_prestige + calculated_nation.get("prestige_gain", 0)
    new_nation["prestige"] = min(max(new_nation["prestige"], 0), 100)
    return ""

def nation_stability_tick(old_nation, new_nation, calculated_nation, schema):
    result = ""
    stability_enum = schema["properties"]["stability"]["enum"]
    stability_index = stability_enum.index(old_nation.get("stability", "Balanced"))

    stability_gain_roll = random.random()
    new_nation["stability_gain_roll"] = stability_gain_roll
    new_nation["stability_gain_chance_at_tick"] = calculated_nation.get("stability_gain_chance", 0)
    if stability_gain_roll <= calculated_nation.get("stability_gain_chance", 0):
        stability_index = min(stability_index + 1, len(stability_enum) - 1)
        result += f"{old_nation.get('name', 'Unknown')} has gained stability.\n"

    stability_loss_roll = random.random()
    new_nation["stability_loss_roll"] = stability_loss_roll
    new_nation["stability_loss_chance_at_tick"] = calculated_nation.get("stability_loss_chance", 0)
    if stability_loss_roll <= calculated_nation.get("stability_loss_chance", 0):
        stability_index = max(stability_index - 1, 0)
        result += f"{old_nation.get('name', 'Unknown')} has lost stability.\n"

    new_nation["stability"] = stability_enum[stability_index]

    return result

def nation_concessions_tick(old_nation, new_nation, calculated_nation, schema):
    if old_nation["overlord"] == "":
        return ""

    result = ""

    if old_nation.get("concessions", {}) != {}:
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
    new_nation["concessions_chance_at_tick"] = calculated_nation.get("concessions_chance", 0)
    if concessions_roll <= calculated_nation.get("concessions_chance", 0):
        concessions_qty = calculated_nation.get("concessions_qty", 0)
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

def nation_rebellion_tick(old_nation, new_nation, calculated_nation, schema):
    if old_nation["overlord"] == "":
        return ""
    result = ""
    rebellion_roll = random.random()
    new_nation["rebellion_roll"] = rebellion_roll
    new_nation["rebellion_chance_at_tick"] = calculated_nation.get("rebellion_chance", 0)
    if rebellion_roll <= calculated_nation.get("rebellion_chance", 0):
        result += f"{old_nation.get('name', 'Unknown')} has rebelled against their overlord.\n"

    return result

def nation_passive_expansion_tick(old_nation, new_nation, calculated_nation, schema):
    result = ""
    expansion_roll = random.random()
    new_nation["expansion_roll"] = expansion_roll
    new_nation["expansion_chance_at_tick"] = calculated_nation.get("passive_expansion_chance", 0)
    if expansion_roll <= calculated_nation.get("passive_expansion_chance", 0):
        result += f"{old_nation.get('name', 'Unknown')} has expanded into adjacent territory.\n"
    return result

def nation_modifier_decay_tick(old_nation, new_nation, calculated_nation, schema):
    new_modifiers = []
    for modifier in old_nation.get("modifiers", []):
        new_modifier = modifier.copy()
        if int(new_modifier["duration"]) > 0:
            new_modifier["duration"] = int(new_modifier["duration"]) - 1
        if int(new_modifier["duration"]) != 0:
            new_modifiers.append(new_modifier)
    new_nation["modifiers"] = new_modifiers
    return ""

def nation_job_cleanup_tick(old_nation, new_nation, calculated_nation, schema):
    new_jobs = {}
    for job in old_nation.get("jobs", {}).keys():
        new_jobs[job] = 0
    new_nation["jobs"] = new_jobs
    return ""

def reset_rolling_karma_to_zero(old_nation, new_nation, schema):
    new_nation["rolling_karma"] = 0

    return ""

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
    "Character Modifier Decay Tick": character_modifier_decay_tick,
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

FACTION_TICK_FUNCTIONS = {
    "Faction Income Tick": faction_income_tick,
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
