from flask import abort
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