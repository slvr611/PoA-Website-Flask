from app_core import mongo
from helpers.change_helpers import request_change, approve_change
import random

def grow_population(nation, foreign_nation):
    pop_roll = random.randint(1, 10)

    roll_modifier = 0
    foreign_acceptance = nation.get("foreign_acceptance", "Integration")
    if foreign_acceptance == "Oppression":
        roll_modifier -= 1
    elif foreign_acceptance == "Harmony":
        roll_modifier += 1
    
    culture = mongo.db.cultures.find_one({"_id": nation.get("primary_culture", "Unknown")})

    if culture:
        trait_1 = culture.get("trait_one", "None")
        trait_2 = culture.get("trait_two", "None")
        trait_3 = culture.get("trait_three", "None")

        if trait_1 == "Mercantilist" or trait_2 == "Mercantilist" or trait_3 == "Mercantilist":
            roll_modifier += 1
        elif trait_1 == "Isolationist" or trait_2 == "Isolationist" or trait_3 == "Isolationist":
            roll_modifier -= 1

    pacted_allies = []

    #TODO: Create a list of pacted allies

    pops = []
    if pop_roll + roll_modifier + len(pacted_allies) >= 9 and pop_roll + roll_modifier < 9:
        pacted_ally = random.choice(pacted_allies)
        pops = list(mongo.db.pops.find({"nation": str(pacted_ally["_id"])}, {"_id": 1, "race": 1, "culture": 1, "religion": 1}))
    elif pop_roll + roll_modifier + len(pacted_allies) >= 9:
        pops = list(mongo.db.pops.find({"nation": str(foreign_nation["_id"])}, {"_id": 1, "race": 1, "culture": 1, "religion": 1}))
    else:
        pops = list(mongo.db.pops.find({"nation": str(nation["_id"])}, {"_id": 1, "race": 1, "culture": 1, "religion": 1}))
    
    new_pop = random.choice(pops).copy()
    new_pop["nation"] = str(nation["_id"])
    new_pop.pop("_id", None)

    change_id = request_change(
        data_type="pops",
        item_id=None,
        change_type="Add",
        before_data={},
        after_data=new_pop,
        reason="Pop Growth for " + nation["name"]
    )
    
    #approve_change(change_id)

    return change_id
    

    