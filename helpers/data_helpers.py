from flask import abort, g
from bson import ObjectId
from app_core import category_data, mongo
from pymongo import ASCENDING

def get_data_on_category(data_type):
    if data_type not in category_data:
        abort(404, "Invalid data type")
    return category_data[data_type]["schema"], category_data[data_type]["database"]

def get_data_on_item(data_type, item_ref):
    schema, db = get_data_on_category(data_type)
    item = None
    try:
        obj_id = ObjectId(item_ref)
        item = db.find_one({"_id": obj_id})
    except:
        item = db.find_one({"name": item_ref})
    if not item:
        abort(404, "Item not found")
    return schema, db, item

def generate_id_to_name_dict(target):
    id_to_name_dict = {}
    all_items = list(category_data[target]["database"].find({}, {"_id": 1, "name": 1}))
    for item in all_items:
        id_to_name_dict[str(item.get("_id", "None"))] = item.get("name", "None")
    return id_to_name_dict

def compute_demographics(nation_id, race_id_to_name, culture_id_to_name, religion_id_to_name):
    demographics = {"race": {}, "culture": {}, "religion": {}}
    pops = list(mongo.db.pops.find({"nation": str(nation_id)}).sort("name", ASCENDING))
    for pop in pops:
        race = race_id_to_name.get(pop.get("race", "Unknown"), "Unknown")
        culture = culture_id_to_name.get(pop.get("culture", "Unknown"), "Unknown")
        religion = religion_id_to_name.get(pop.get("religion", "Unknown"), "Unknown")

        demographics["race"][race] = demographics["race"].get(race, 0) + 1
        demographics["culture"][culture] = demographics["culture"].get(culture, 0) + 1
        demographics["religion"][religion] = demographics["religion"].get(religion, 0) + 1
    return demographics

def get_dropdown_options(schema):
    """Helper function to get dropdown options for linked fields"""
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collection"):
            related_collection = attributes.get("collection")
            dropdown_options[field] = list(
                mongo.db[related_collection].find(
                    {}, {"name": 1, "_id": 1}
                ).sort("name", ASCENDING)
            )
    return dropdown_options

def get_user_entities():
    """Wrapper to get current user's entities for the navbar"""
    if not g.user:
        return None
        
    # Get player's database ID
    player = mongo.db.players.find_one({"id": g.user.get("id")})
    if not player:
        return None
    
    player_id = str(player["_id"])
    
    # Get all characters owned by player
    characters = list(mongo.db.characters.find(
        {"player": player_id},
        {"name": 1, "ruling_nation_org": 1}
    ).sort("name", ASCENDING))
    
    # Get all nations/orgs ruled by player's characters
    ruled_entity_ids = [str(char.get("ruling_nation_org")) for char in characters if char.get("ruling_nation_org")]
    
    nations = list(mongo.db.nations.find(
        {"_id": {"$in": [ObjectId(id) for id in ruled_entity_ids]}},
        {"name": 1}
    ).sort("name", ASCENDING))
    
    mercenaries = list(mongo.db.mercenaries.find(
        {"_id": {"$in": [ObjectId(id) for id in ruled_entity_ids]}},
        {"name": 1}
    ).sort("name", ASCENDING))

    merchants = list(mongo.db.merchants.find(
        {"_id": {"$in": [ObjectId(id) for id in ruled_entity_ids]}},
        {"name": 1}
    ).sort("name", ASCENDING))

    factions = list(mongo.db.factions.find(
        {"_id": {"$in": [ObjectId(id) for id in ruled_entity_ids]}},
        {"name": 1}
    ).sort("name", ASCENDING))


    
    return {
        "characters": characters,
        "nations": nations,
        "mercenaries": mercenaries,
        "merchants": merchants,
        "factions": factions
    }
