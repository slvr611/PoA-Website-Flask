from helpers.data_helpers import get_data_on_category
from pymongo import ASCENDING
from helpers.change_helpers import request_change, approve_change
from app_core import mongo
from flask import flash
from app_core import backup_mongodb
import random

def tick(form_data):
    backup_database()
    tick_summary = []

    old_characters = list(mongo.db.characters.find().sort("name", ASCENDING))
    new_characters = old_characters.deepcopy()

    for tick_function_label, tick_function in CHARACTER_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            for i in range(len(old_characters)):
                tick_summary += tick_function(old_characters[i], new_characters[i])
    
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
    
    old_merchants = list(mongo.db.merchants.find().sort("name", ASCENDING))
    new_merchants = old_merchants.deepcopy()

    for tick_function_label, tick_function in MERCHANT_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            for i in range(len(old_merchants)):
                tick_summary += tick_function(old_merchants[i], new_merchants[i])
    
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
    
    old_mercenaries = list(mongo.db.mercenaries.find().sort("name", ASCENDING))
    new_mercenaries = old_mercenaries.deepcopy()

    for tick_function_label, tick_function in MERCENARY_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            for i in range(len(old_mercenaries)):
                tick_summary += tick_function(old_mercenaries[i], new_mercenaries[i])
    
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
    
    old_nations = list(mongo.db.nations.find().sort("name", ASCENDING))
    new_nations = old_nations.deepcopy()

    for tick_function_label, tick_function in NATION_TICK_FUNCTIONS.items():
        run_key = f"run_{tick_function_label}"
        if run_key in form_data:
            for i in range(len(old_nations)):
                tick_summary += tick_function(old_nations[i], new_nations[i])
    
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
    return

def give_tick_summary(tick_summary):
    for item in tick_summary:
        flash(item)
    return

###########################################################
# Character Tick Functions
###########################################################

def character_death_tick(old_character, new_character):
    return

def character_heal_tick(old_character, new_character):
    return

def character_mana_tick(old_character, new_character):
    return

def character_age_tick(old_character, new_character):
    return

###########################################################
# Merchant Tick Functions
###########################################################

def merchant_income_tick(old_merchant, new_merchant):
    return

###########################################################
# Mercenary Tick Functions
###########################################################

def mercenary_upkeep_tick(old_mercenary, new_mercenary):
    return

###########################################################
# Nation Tick Functions
###########################################################

def nation_income_tick(old_nation, new_nation):
    return

def update_rolling_karma():
    schema, db = get_data_on_category("nations")
    nations = list(db.find().sort("name", ASCENDING))

    for nation in nations:
        new_nation = nation.copy()
        event_type = nation.get("event_type", "Unknown")
        if event_type in ["Horrendous", "Abysmal", "Very Bad", "Bad"]:
            new_nation["rolling_karma"] = int(nation.get("rolling_karma", 0)) + 1

            change_id = request_change(
                data_type="nations",
                item_id=nation["_id"],
                change_type="Update",
                before_data=nation,
                after_data=new_nation,
                reason="Rolling Karma Update for " + nation["name"]
            )
            approve_change(change_id)
        elif event_type in ["Good", "Very Good", "Fantastic", "Wonderous"]:
            new_nation["rolling_karma"] = int(nation.get("rolling_karma", 0)) - 1
            change_id = request_change(
                data_type="nations",
                item_id=nation["_id"],
                change_type="Update",
                before_data=nation,
                after_data=new_nation,
                reason="Rolling Karma Update for " + nation["name"]
            )
            approve_change(change_id)

    return

def nation_infamy_decay_tick(old_nation, new_nation):
    return

def nation_prestige_gain_tick(old_nation, new_nation):
    return

def nation_stability_tick(old_nation, new_nation):
    return

def nation_concessions_tick(old_nation, new_nation):
    return

def nation_rebellion_tick(old_nation, new_nation):
    return

def nation_passive_growth_tick(old_nation, new_nation):
    return

def nation_modifier_decay_tick(old_nation, new_nation):
    return

def nation_job_cleanup_tick(old_nation, new_nation):
    return

def reset_rolling_karma_to_zero():
    schema, db = get_data_on_category("nations")
    nations = list(db.find().sort("name", ASCENDING))

    for nation in nations:
        new_nation = nation.copy()
        new_nation["rolling_karma"] = 0
        change_id = request_change(
            data_type="nations",
            item_id=nation["_id"],
            change_type="Update",
            before_data=nation,
            after_data=new_nation,
            reason="Resetting Rolling Karma for " + nation["name"]
        )
        approve_change(change_id)

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
    "Nation Passive Growth Tick": nation_passive_growth_tick,
    "Nation Modifier Decay Tick": nation_modifier_decay_tick,
    "Nation Job Cleanup Tick": nation_job_cleanup_tick,
    "Nation Reset Rolling Karma to Zero (Generally Don't Use)": reset_rolling_karma_to_zero,
}
