from bson import ObjectId
from app_core import mongo
from helpers.change_helpers import system_request_change, system_approve_change
from helpers.data_helpers import get_data_on_category
from pymongo import ASCENDING
from threading import Thread
import random

def grow_all_population_async(form_data):
    thread = Thread(target=grow_all_population, args=(form_data,))
    thread.daemon = True
    thread.start()
    return "Population growth process started in background. Check logs for results."

def grow_all_population(form_data):
    schema, db = get_data_on_category("nations")
    nations = list(db.find().sort("name", ASCENDING))
    
    changes = []
    for nation in nations:
        nation_id = str(nation["_id"])
        include_key = f"include_{nation_id}"
        foreign_source_key = f"foreign_source_{nation_id}"
        
        # Check if this nation should be included in growth
        if include_key in form_data:
            foreign_nation_id = form_data.get(foreign_source_key)
            
            # Only process if a foreign nation was selected
            if foreign_nation_id:
                try:
                    foreign_nation = db.find_one({"_id": ObjectId(foreign_nation_id)})
                except:
                    foreign_nation = None
                if foreign_nation:
                    change_id = grow_population(nation, foreign_nation)
                    changes.append({
                        "nation": nation["name"],
                        "foreign_nation": foreign_nation["name"],
                        "change_id": change_id
                    })
    return changes    

def grow_population(nation, foreign_nation):
    pop_roll = random.randint(1, 10)

    roll_modifier = 0
    foreign_acceptance = nation.get("foreign_acceptance", "Integration")
    if foreign_acceptance == "Oppression":
        roll_modifier -= 1
    elif foreign_acceptance == "Harmony":
        roll_modifier += 1
    try:
        culture = mongo.db.cultures.find_one({"_id": ObjectId(nation.get("primary_culture", ""))})
    except:
        culture = None

    if culture:
        trait_1 = culture.get("trait_one", "None")
        trait_2 = culture.get("trait_two", "None")
        trait_3 = culture.get("trait_three", "None")

        if trait_1 == "Mercantilist" or trait_2 == "Mercantilist" or trait_3 == "Mercantilist":
            roll_modifier += 1
        elif trait_1 == "Isolationist" or trait_2 == "Isolationist" or trait_3 == "Isolationist":
            roll_modifier -= 1

    pacted_allies = []

    try:
        pacted_allies = list(mongo.db.diplo_relations.find({"nation_1": ObjectId(nation["_id"]), "pact_type": {"$in": ["Defensive Pact", "Military Alliance"]}}, {"nation_2": 1}))
        pacted_allies += list(mongo.db.diplo_relations.find({"nation_2": ObjectId(nation["_id"]), "pact_type": {"$in": ["Defensive Pact", "Military Alliance"]}}, {"nation_1": 1}))
    except:
        pass

    pacted_allies = [ally.get("nation_1", "") or ally.get("nation_2", "") for ally in pacted_allies]

    pops = []
    if pop_roll + roll_modifier + len(pacted_allies) >= 9 and pop_roll + roll_modifier < 9:
        pacted_ally = random.choice(pacted_allies)
        print(pacted_ally)
        try:
            pops = list(mongo.db.pops.find({"nation": ObjectId(pacted_ally)}, {"_id": 1, "race": 1, "culture": 1, "religion": 1}))
            if len(pops) == 0:
                pops = [{"race": pacted_ally.get("primary_race", ""), "culture": pacted_ally.get("primary_culture", ""), "religion": pacted_ally.get("primary_religion", "")}]
        except:
            pass
    elif pop_roll + roll_modifier + len(pacted_allies) >= 9:
        try:
            pops = list(mongo.db.pops.find({"nation": ObjectId(foreign_nation["_id"])}, {"_id": 1, "race": 1, "culture": 1, "religion": 1}))
            if len(pops) == 0:
                pops = [{"race": foreign_nation.get("primary_race", ""), "culture": foreign_nation.get("primary_culture", ""), "religion": foreign_nation.get("primary_religion", "")}]
        except:
            pass
    else:
        try:
            pops = list(mongo.db.pops.find({"nation": ObjectId(nation["_id"])}, {"_id": 1, "race": 1, "culture": 1, "religion": 1}))
            if len(pops) == 0:
                pops = [{"race": nation.get("primary_race", ""), "culture": nation.get("primary_culture", ""), "religion": nation.get("primary_religion", "")}]
        except:
            pass
    
    new_pop = random.choice(pops).copy()
    new_pop["nation"] = str(nation["_id"])
    new_pop.pop("_id", None)

    change_id = system_request_change(
        data_type="pops",
        item_id=None,
        change_type="Add",
        before_data={},
        after_data=new_pop,
        reason="Pop Growth for " + nation["name"]
    )
    
    system_approve_change(change_id)

    return change_id
    
def roll_events_async():
    thread = Thread(target=roll_events)
    thread.daemon = True
    thread.start()
    return "Event roll process started in background. Check logs for results."

def roll_events():
    schema, db = get_data_on_category("nations")

    nations = list(db.find().sort("name", ASCENDING))

    for nation in nations:
        raw_roll = random.randint(1, 20)
        event_roll = raw_roll + nation.get("karma", 0)
        event_type = "Unknown"
        if raw_roll == 20 and event_roll >= 30:
            event_type = "Wonderous"
        elif raw_roll == 20 or event_roll >= 23:
            event_type = "Fantastic"
        elif raw_roll == 1:
            event_type = "Abysmal"
        elif event_roll >= 18:
            event_type = "Very Good"
        elif event_roll >= 15:
            event_type = "Good"
        elif event_roll >= 7:
            event_type = "Neutral"
        elif event_roll >= 4:
            event_type = "Bad"
        elif event_roll >= 2:
            event_type = "Very Bad"
        elif event_roll <= 1:
            event_type = "Abysmal"
        elif event_roll <= -10:
            event_type = "Horrendous"

        new_nation = nation.copy()
        new_nation.update({"temporary_karma": 0, "event_roll": event_roll, "raw_roll": raw_roll, "event_type": event_type,
                           "previous_karma": nation.get("karma", 0), "previous_temporary_karma": nation.get("temporary_karma", 0), "previous_rolling_karma": nation.get("rolling_karma", 0)})
        change_id = system_request_change(
            data_type="nations",
            item_id=nation["_id"],
            change_type="Update",
            before_data=nation,
            after_data=new_nation,
            reason="Karma Roll for " + nation["name"]
        )
        system_approve_change(change_id)
    
    pass