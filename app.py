from flask import Flask, redirect, url_for, session, request, render_template, g, flash, abort
from flask_discord import DiscordOAuth2Session, requires_authorization, Unauthorized
from flask_pymongo import PyMongo
from werkzeug.routing import BaseConverter
from pymongo import ASCENDING, DESCENDING
from dotenv import load_dotenv
from jsonschema import validate, ValidationError
from bson import ObjectId
from datetime import datetime
import math
import os
import json
import copy

#Load environment variables from .env file
load_dotenv()

#Initialize the Flask app
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey")

#Discord OAuth configuration
app.config["DISCORD_CLIENT_ID"] = os.getenv("DISCORD_CLIENT_ID")
app.config["DISCORD_CLIENT_SECRET"] = os.getenv("DISCORD_CLIENT_SECRET")
app.config["DISCORD_REDIRECT_URI"] = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:5000/auth/discord/callback")

discord = DiscordOAuth2Session(app)

#MongoDB configuration
app.config["MONGO_URI"] = os.getenv("MONGO_URI", "mongodb://localhost:27017/flask_discord_app")
mongo = PyMongo(app)

#Constants
category_data = {
    "nations": {"pluralName": "Nations", "singularName": "Nation", "database": mongo.db.nations},
    "regions": {"pluralName": "Regions", "singularName": "Region", "database": mongo.db.regions},
    "races": {"pluralName": "Races", "singularName": "Race", "database": mongo.db.races},
    "cultures": {"pluralName": "Cultures", "singularName": "Culture", "database": mongo.db.cultures},
    "religions": {"pluralName": "Religions", "singularName": "Religion", "database": mongo.db.religions},
    "merchants": {"pluralName": "Merchants", "singularName": "Merchant", "database": mongo.db.merchants},
    "mercenaries": {"pluralName": "Mercenaries", "singularName": "Mercenary", "database": mongo.db.mercenaries},
    "factions": {"pluralName": "Factions", "singularName": "Faction", "database": mongo.db.factions},
    "characters": {"pluralName": "Characters", "singularName": "Character", "database": mongo.db.characters},
    "players": {"pluralName": "Players", "singularName": "Player", "database": mongo.db.players},
    "artifacts": {"pluralName": "Artifacts", "singularName": "Artifact", "database": mongo.db.artifacts},
    "spells": {"pluralName": "Spells", "singularName": "Spell", "database": mongo.db.spells},
    "wonders": {"pluralName": "Wonders", "singularName": "Wonder", "database": mongo.db.wonders},
    "markets": {"pluralName": "Markets", "singularName": "Market", "database": mongo.db.markets},
    "wars": {"pluralName": "Wars", "singularName": "War", "database": mongo.db.wars},
    "war_links": {"pluralName": "War Links", "singularName": "War Link", "database": mongo.db.war_links},
    "market_links": {"pluralName": "Market Links", "singularName": "Market Link", "database": mongo.db.market_links},
    "diplo_relation": {"pluralName": "Diplomatic Relations", "singularName": "Diplomatic Relation", "database": mongo.db.diplo_relation},
    "pops": {"pluralName": "Pops", "singularName": "Pop", "database": mongo.db.pops},
    "trades": {"pluralName": "Trades", "singularName": "Trade", "database": mongo.db.trades},
    "events": {"pluralName": "Events", "singularName": "Event", "database": mongo.db.events},
    "units": {"pluralName": "Units", "singularName": "Unit", "database": mongo.db.units},
    "districts": {"pluralName": "Districts", "singularName": "District", "database": mongo.db.districts},
    "changes": {"pluralName": "Changes", "singularName": "Change", "database": mongo.db.changes}
}

json_files = ["jobs", "districts"]
json_data = {}

def load_json(file_path):
    with open(file_path, "r") as file:
        json_data = json.load(file)
    return json_data

for data_type in category_data:
    category_data[data_type]["schema"] = load_json("json-data/schemas/" + data_type + ".json")["$jsonSchema"]

for file in json_files:
    json_data[file] = load_json("json-data/" + file + ".json")

general_resources = [
    {"key": "food", "name": "Food", "base_storage": 20},
    {"key": "wood", "name": "Wood", "base_storage": 15},
    {"key": "stone", "name": "Stone", "base_storage": 15},
    {"key": "mounts", "name": "Mounts", "base_storage": 10},
    {"key": "research", "name": "Research", "base_storage": 0},
    {"key": "magic", "name": "Magic", "base_storage": 10}
]

unique_resources = [
    {"key": "bronze", "name": "Bronze", "base_storage": 5},
    {"key": "iron", "name": "Iron", "base_storage": 0},
]

@app.context_processor
def inject_navbar_pages():
    return {'category_data': category_data}

#Middleware
@app.before_request
def load_user():
    g.user = session.get('user', None)

@app.before_request
def cache_previous_url():
    if request.endpoint != 'static':
        session['second_previous_url'] = session.get('previous_url', None)
        session['previous_url'] = session.get('current_url', None)
        session['current_url'] = request.url

#Routes
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/go_back")
def go_back():
    previous_url = session.get('second_previous_url', url_for("home"))
    return redirect(previous_url)


#######################################################

def get_data_on_category(data_type):
    if data_type not in category_data:
        abort(404, "Invalid data type")
    
    schema = category_data[data_type]["schema"]
    db = category_data[data_type]["database"]
    
    return schema, db

def get_data_on_item(data_type, item_ref):
    schema, db = get_data_on_category(data_type)
    
    item = None
    try:
        obj_id = ObjectId(item_ref)
        item = db.find_one({"_id": obj_id})
    except:
        #If not a valid ObjectId, fallback to name
        item = db.find_one({"name": item_ref})
    
    if not item:
        abort(404, "Item not found")
    
    return schema, db, item

def generate_id_to_name_dict(target):
    id_to_name_dict = {}
    all_items = list(category_data[target]["database"].find({}, {"_id": 1, "name": 1}))
    
    for item in all_items:
        id_to_name_dict[str(item.get("_id", "None"))] = item.get("name", "None")
    
    print(id_to_name_dict)
    
    return id_to_name_dict

def compute_demographics(nation_id, race_id_to_name, culture_id_to_name, religion_id_to_name):
    demographics = {"race": {}, "culture": {}, "religion": {}}
    pops = list(mongo.db.pops.find({"nation": str(nation_id)}))
    for pop in pops:
        race = race_id_to_name.get(pop.get("race", "Unknown"), "Unknown")
        culture = culture_id_to_name.get(pop.get("culture", "Unknown"), "Unknown")
        religion = religion_id_to_name.get(pop.get("religion", "Unknown"), "Unknown")
        
        demographics["race"][race] = demographics["race"].get(race, 0) + 1
        demographics["culture"][culture] = demographics["culture"].get(culture, 0) + 1
        demographics["religion"][religion] = demographics["religion"].get(religion, 0) + 1
    return demographics

#######################################################

@app.route("/demographics_overview")
def demographics_overview():
    nations = list(mongo.db.nations.find().sort("name", ASCENDING))
    
    race_id_to_name = generate_id_to_name_dict("races")
    culture_id_to_name = generate_id_to_name_dict("cultures")
    religion_id_to_name = generate_id_to_name_dict("religions")
    
    demographics_list = []
    for nation in nations:
        demo = compute_demographics(nation.get("_id", None), race_id_to_name, culture_id_to_name, religion_id_to_name)
        demographics_list.append({
            "name": nation["name"],
            "demographics": demo
        })
    
    print(demographics_list)
    
    return render_template("demographics_overview.html", demographics_list=demographics_list)


@app.route("/<data_type>")
def data_list(data_type):
    schema, db = get_data_on_category(data_type)
    
    query_dict = {"_id": 1, "name": 1}
    preview_overall_lookup_dict = {}
    
    for preview_item in schema.get("preview", {}):
        query_dict[preview_item] = 1
        collection_name = schema.get("properties", {}).get(preview_item, {}).get("collection", None)
        if not collection_name is None:
            preview_db = category_data[collection_name]["database"]
            preview_individual_lookup_dict = {}
            preview_data = list(preview_db.find({}, {"_id": 1, "name": 1}))
            for data in preview_data:
                preview_individual_lookup_dict[str(data["_id"])] = {"name": data.get("name", "None"), "link": collection_name + "/item/" + data.get("name", data.get("_id", "#"))}
            preview_overall_lookup_dict[preview_item] = preview_individual_lookup_dict
    
    items = list(db.find({}, query_dict).sort("name", ASCENDING))
    
    return render_template(
        "dataList.html",
        title=category_data[data_type]["pluralName"],
        items=items,
        schema=schema,
        preview_references=preview_overall_lookup_dict
    )

#######################################################

@app.route("/<data_type>/edit")
def data_list_edit(data_type):
    schema, db = get_data_on_category(data_type)
    
    query_dict = {"_id": 1, "name": 1}
    preview_overall_lookup_dict = {}
    
    for preview_item in schema.get("preview", {}):
        query_dict[preview_item] = 1
        collection_name = schema.get("properties", {}).get(preview_item, {}).get("collection", None)
        if not collection_name is None:
            preview_db = category_data[collection_name]["database"]
            preview_individual_lookup_dict = {}
            preview_data = list(preview_db.find({}, {"_id": 1, "name": 1}))
            for data in preview_data:
                preview_individual_lookup_dict[str(data["_id"])] = {data.get("name", "None"), collection_name + "/item/" + data.get("name", data.get("_id", "#"))}
            preview_overall_lookup_dict[preview_item] = preview_individual_lookup_dict
    
    items = list(db.find({}, query_dict).sort("name", ASCENDING))

    return render_template(
        "dataListEdit.html",
        title=category_data[data_type]["pluralName"],
        items=items,
        schema=schema,
        preview_references=preview_overall_lookup_dict
    )

#######################################################

@app.route("/<data_type>/new", methods=["GET"])
def data_item_new(data_type):
    schema, db = get_data_on_category(data_type)
    
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collection") != None:
            related_collection = attributes.get("collection")
            dropdown_options[field] = list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}))
    
    template = render_template(
        "dataItemNew.html",
        title="New " + category_data[data_type]["singularName"],
        schema=schema,
        dropdown_options=dropdown_options
    )
    
    return template

@app.route("/<data_type>/new/request", methods=["POST"])
def data_item_new_request(data_type):
    schema, db = get_data_on_category(data_type)
    
    form_data = request.form.to_dict()
    
    try:
        validate(instance=form_data, schema=schema)
    except ValidationError as e:
        flash(f"Validation Error: {e.message}")
        return redirect("/" + data_type + "/new")
    
    if "name" in form_data and db.find_one({"name": form_data["name"]}):
        flash("Name must be unique!")
        return redirect("/" + data_type + "/new")
    
    reason = form_data.get("reason", "No Reason Given")
    after_data = form_data
    
    change_id = request_change(
        data_type=data_type,
        item_id=None,
        change_type="Add",
        before_data={},
        after_data=after_data,
        reason=reason
    )
    
    flash(f"Create request #{change_id} created and awaits admin approval.")
    
    return redirect("/" + data_type)

@app.route("/<data_type>/new/save", methods=["POST"])
def data_item_new_approve(data_type):
    schema, db = get_data_on_category(data_type)
    
    form_data = request.form.to_dict()
    
    try:
        validate(instance=form_data, schema=schema)
    except ValidationError as e:
        flash(f"Validation Error: {e.message}")
        return redirect("/" + data_type + "/new")
    
    if "name" in form_data and db.find_one({"name": form_data["name"]}):
        flash("Name must be unique!")
        return redirect("/" + data_type + "/new")
    
    reason = form_data.get("reason", "No Reason Given")
    after_data = form_data
    
    change_id = request_change(
        data_type=data_type,
        item_id=None,
        change_type="Add",
        before_data={},
        after_data=after_data,
        reason=reason
    )
    
    approve_change(change_id)
    
    flash(f"Create request #{change_id} created and approved.")
    
    return redirect("/" + data_type)

#######################################################

@app.route("/changes/item/<item_ref>")
def change_item(item_ref):
    schema, db, item = get_data_on_item("changes", item_ref)
    
    target_schema = None
    
    if item["target_collection"] in category_data:
        target_schema = category_data[item["target_collection"]]["schema"]
    
    linked_objects = get_linked_objects(schema, item)
    
    if item["target_collection"] != None and item["target"] != None:
        obj = mongo.db[item["target_collection"]].find_one({"_id": item["target"]}, {"name": 1, "_id": 1})
        if obj is not None:
            if "name" in obj:
                linked_objects["target"] = {"name": obj["name"], "link": f"/{item["target_collection"]}/item/{obj['name']}"}
            else:
                linked_objects["target"] = {"name": obj["_id"], "link": f"/{item["target_collection"]}/item/{obj['_id']}"}
        else:
            linked_objects["target"] = {"name": item["target"]}
            
    
    return render_template(
        "change.html",
        title=item_ref,
        schema=schema,
        item=item,
        linked_objects=linked_objects,
        target_schema=target_schema
    )

@app.route("/changes/item/<item_ref>/approve")
def approve_change_route(item_ref):
    schema, db, item = get_data_on_item("changes", item_ref)
    
    try:
        obj_id = ObjectId(item_ref)
        approve_change(obj_id)
    except Exception as e:
        print(f"Error converting {object_id_to_find} to ObjectId: {e}")
    
    return redirect("/changes")

@app.route("/nations/item/<item_ref>")
def nation_item(item_ref):
    schema, db, nation = get_data_on_item("nations", item_ref)
    
    linked_objects = get_linked_objects(schema, nation)
    
    calculated_fields = calculate_all_fields(nation, schema)
    nation.update(calculated_fields)
    
    if "jobs" not in nation:
        nation["jobs"] = {}
    
    return render_template(
        "nation.html",
        title=item_ref,
        schema=schema,
        nation=nation,
        linked_objects=linked_objects,
        general_resources=general_resources,
        unique_resources=unique_resources,
        districts_config=json_data["districts"]
    )


@app.route("/<data_type>/item/<item_ref>")
def data_item(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    
    linked_objects = get_linked_objects(schema, item)
    
    calcuated_fields = calculate_all_fields(item, schema)
    item.update(calcuated_fields)
    
    return render_template(
        "dataItem.html",
        title=item_ref,
        schema=schema,
        item=item,
        linked_objects=linked_objects
    )

def get_linked_objects(schema, item):
    properties = schema.get("properties", {})
    linked_objects = {}
        
    for field, attributes in properties.items():
        if attributes.get("collection") != None:
            related_collection = attributes.get("collection")
            
            if attributes.get("queryTargetAttribute") != None:
                query_target = attributes.get("queryTargetAttribute")
                item_id = str(item["_id"])
                
                query_dict = {"name": 1, "_id": 1}
                
                field_preview = attributes.get("preview", None)
                
                if field_preview is not None:
                    for preview_item in field_preview:
                        query_dict[preview_item] = 1
                
                related_items = list(mongo.db[related_collection].find({query_target: item_id}, query_dict))
                
                if related_items:
                    linked_objects[field] = []
                    for obj in related_items:
                        object_to_add = {}
                        if "name" in obj:
                            object_to_add = {"name": obj["name"], "link": f"/{related_collection}/item/{obj['name']}"}
                        else:
                            object_to_add = {"name": obj["_id"], "link": f"/{related_collection}/item/{obj['_id']}"}
                        if attributes.get("preview", None) is not None:
                            preview_schema = category_data[attributes.get("collection")]["schema"]
                            object_to_add["linked_objects"] = get_linked_objects(preview_schema, obj)
                        linked_objects[field].append(object_to_add)
            
            else:
                if field in item:
                    object_id_to_find = item[field]
                    
                    if isinstance(object_id_to_find, str):
                        try:
                            object_id_to_find = ObjectId(object_id_to_find)
                        except Exception as e:
                            print(f"Error converting {object_id_to_find} to ObjectId: {e}")
                            continue
                    
                    linked_object = mongo.db[related_collection].find_one({"_id": object_id_to_find})
                    
                    if linked_object is not None:
                        linked_object["link"] = "/" + related_collection + "/item/" + linked_object["name"] #TODO:  Update this to support ids
                    
                    linked_objects[field] = linked_object
    
    return linked_objects

#######################################################

@app.route("/nations/edit/<item_ref>", methods=["GET"])
def edit_nation(item_ref):
    schema, db, nation = get_data_on_item("nations", item_ref)
    
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collection") != None:
            related_collection = attributes.get("collection")
            dropdown_options[field] = list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}))
    
    linked_objects = get_linked_objects(schema, nation)
    
    calculated_fields = calculate_all_fields(nation, schema)
    nation.update(calculated_fields)
    
    template = render_template(
        "nationEdit.html",
        title="Edit " + item_ref,
        schema=schema,
        nation=nation,
        dropdown_options=dropdown_options,
        linked_objects=linked_objects,
        general_resources=general_resources,
        unique_resources=unique_resources,
        districts_config=json_data["districts"]
    )
    return template


@app.route("/<data_type>/edit/<item_ref>", methods=["GET"])
def data_item_edit(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collection") != None:
            related_collection = attributes.get("collection")
            dropdown_options[field] = list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}))
    
    linked_objects = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collection") != None and attributes.get("queryTargetAttribute") != None:
            related_collection = attributes.get("collection")
            query_target = attributes.get("queryTargetAttribute")
            item_id = str(item["_id"])
            linked_objects[field] = list(mongo.db[related_collection].find({query_target: item_id}, {"name": 1, "_id": 1}))
    
    template = render_template(
        "dataItemEdit.html",
        title="Edit " + item_ref,
        schema=schema,
        item=item,
        dropdown_options=dropdown_options,
        linked_objects=linked_objects
    )
    return template



@app.route("/<data_type>/edit/<item_ref>/request", methods=["POST"])
def data_item_edit_request(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    
    form_data = request.form.to_dict()
    
    try:
        validate(instance=form_data, schema=schema)
    except ValidationError as e:
        flash(f"Validation Error: {e.message}")
        return redirect("/" + data_type + "/edit/" + item_ref)
    
    if "name" in form_data and form_data["name"] != item_ref and db.find_one({"name": form_data["name"]}):
        flash("Name must be unique!")
        return redirect(f"/{data_type}/edit/{item_ref}")
    
    item_id = item["_id"]
    reason = form_data.get("reason", "No Reason Given")
    before_data = item
    after_data = form_data
    
    change_id = request_change(
        data_type=data_type,
        item_id=item_id,
        change_type="Update",
        before_data=before_data,
        after_data=after_data,
        reason=reason
    )
    
    flash(f"Change request #{change_id} created and awaits admin approval.")
    
    return redirect(f"/{data_type}")



@app.route("/<data_type>/edit/<item_ref>/save", methods=["POST"])
def data_item_edit_approve(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    
    form_data = request.form.to_dict()
    
    try:
        validate(instance=form_data, schema=schema)
    except ValidationError as e:
        flash(f"Validation Error: {e.message}")
        return redirect("/" + data_type + "/edit/" + item_ref)
    
    if "name" in form_data and form_data["name"] != item_ref and db.find_one({"name": form_data["name"]}):
        flash("Name must be unique!")
        return redirect(f"/{data_type}/edit/{item_ref}")
    
    item_id = item["_id"]
    reason = form_data.get("reason", "No Reason Given")
    before_data = item
    after_data = form_data
    
    change_id = request_change(
        data_type=data_type,
        item_id=item_id,
        change_type="Update",
        before_data=before_data,
        after_data=after_data,
        reason=reason
    )
    
    approve_change(change_id)
    
    flash(f"Change request #{change_id} created and approved.")
    
    return redirect("/" + data_type)

#######################################################

#Helper function to request a change
def request_change(data_type, item_id, change_type, before_data, after_data, reason):
    requester = mongo.db.players.find_one({"id": g.user["id"]})["_id"]
    if requester is None:
        return None
    
    #TODO: Check requester perms
    
    changes_collection = mongo.db.changes
    now = datetime.utcnow()

    after_data.pop("reason", None)
    before_data.pop("_id", None)
    after_data.pop("_id", None) #Delete the reason and id so that they don't show up in the final change log
    
    before_data, after_data = keep_only_differences(before_data, after_data, change_type)
    
    differential = calculate_int_changes(before_data, after_data)
    
    change_doc = {
        "target_collection": data_type,
        "target": item_id,
        "time_requested": now,
        "requester": requester,
        "change_type": change_type,
        "before_requested_data": before_data,
        "after_requested_data": after_data,
        "differential_data": differential,
        "request_reason": reason,
        "status": "Pending"
    }
    
    result = changes_collection.insert_one(change_doc)
    return result.inserted_id

#Returns true if worked ok, if rejected returns false
def approve_change(change_id):
    approver = mongo.db.players.find_one({"id": g.user["id"]}, {"_id": 1, "username": 1, "is_admin": 1})
    
    if approver is None:
        print("Change approval failed, no logged in user!")
        return None
    
    if not approver.get("is_admin", False):
        print("Change approval failed, logged in user is not admin!")
        return None
    
    changes_collection = mongo.db.changes
    now = datetime.utcnow()
    
    change = changes_collection.find_one({"_id": change_id})
    target_collection = category_data[change["target_collection"]]["database"]

    if change["change_type"] == "Add":
        before_data = change["before_requested_data"]
        after_data = change["after_requested_data"]

        inserted_item_id = target_collection.insert_one(after_data).inserted_id
        changes_collection.update_one(
            {"_id": change_id},
            {"$set": {
            "target": inserted_item_id,
            "status": "Approved",
            "time_implemented": now,
            "approver": approver.get("_id", None),
            "before_implemented_data": before_data,
            "after_implemented_data": after_data,
            }}
        )
        
        return True

    else: #Not add means it's update or remove, which means there's a target ahead of time
        target = target_collection.find_one({"_id": change["target"]})
        
        before_requested_data = change["before_requested_data"]
        after_requested_data = change["after_requested_data"]
        
        if check_no_other_changes(before_requested_data, after_requested_data, target):
            
            #TODO: Check for whether after_requested needs to be updated using differential because before_requested changed (I hope this makes sense to future Orion) also update before requested for the sake of the logs
            
            before_data = before_requested_data
            after_data = after_requested_data
            
            if change["change_type"] == "Update":
                target_collection.update_one(
                    {"_id": change["target"]},
                    {"$set": after_data}
                )
            else: #Not update means its remove
                target_collection.delete_one(
                    {"_id": change["target"]}
                )
            
            changes_collection.update_one(
                {"_id": change_id},
                {"$set": {
                "status": "Approved",
                "time_implemented": now,
                "approver": approver.get("_id", None),
                "before_implemented_data": before_data,
                "after_implemented_data": after_data,
                }}
            )
            
            return True
    
    print("Failed to approve")
    return False

def keep_only_differences(before_data, after_data, change_type):
    new_before = {}
    new_after = {}
    
    if change_type == "Remove":
        for key in set(before_data.keys()):
            new_before[key] = before_data.get(key)
            new_after[key] = after_data.get(key)

    else:
        for key in set(after_data.keys()):
            if(before_data.get(key) != after_data.get(key)):
                new_before[key] = before_data.get(key)
                new_after[key] = after_data.get(key)

    return new_before, new_after

def calculate_int_changes(before_data, after_data):
    diff = {}
    for key in set(before_data.keys()) | set(after_data.keys()):
        before_val = before_data.get(key)
        after_val = after_data.get(key)
        if isinstance(before_val, int) and isinstance(after_val, int):
            diff[key] = {after_val - before_val}
    return diff

#Returns true if no other changes have been made that will interfere with approving a change
def check_no_other_changes(before_data, after_data, current_data):
    for key in before_data:
        before_val = before_data.get(key, None)
        after_val = after_data.get(key, None)
        current_val = current_data.get(key, None)
        if before_val != current_val and after_val != current_val and not isinstance(before_val, int) and not isinstance(after_val, int) and not isinstance(current_val, int):
            return False
    return True

#######################################################

@app.route("/<data_type>/clone/<item_ref>/request", methods=["POST"])
def data_item_clone_request(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    
    form_data = request.form.to_dict()
    
    if "name" in item:
        item["name"] = "Copy of " + item["name"]
    
    item_id = item["_id"]
    reason = form_data.get("reason", "No Reason Given")
    after_data = item
    
    change_id = request_change(
        data_type=data_type,
        item_id=None,
        change_type="Add",
        before_data={},
        after_data=after_data,
        reason=reason
    )
    
    flash(f"Change request #{change_id} created and awaits admin approval.")
    
    return redirect("/go_back")

@app.route("/<data_type>/clone/<item_ref>/save", methods=["POST"])
def data_item_clone_approve(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    
    form_data = request.form.to_dict()
    
    if "name" in item:
        item["name"] = "Copy of " + item["name"]
    
    item_id = item["_id"]
    reason = form_data.get("reason", "No Reason Given")
    after_data = item
    
    change_id = request_change(
        data_type=data_type,
        item_id=None,
        change_type="Add",
        before_data={},
        after_data=after_data,
        reason=reason
    )
    
    approve_change(change_id)
    
    flash(f"Change request #{change_id} created and approved.")
    
    return redirect("/go_back")

@app.route("/<data_type>/delete/<item_ref>/request", methods=["POST"])
def data_item_delete_request(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)

    form_data = request.form.to_dict()
    
    item_id = item["_id"]
    reason = form_data.get("reason", "No Reason Given")
    before_data = item
    after_data = form_data
    
    change_id = request_change(
        data_type=data_type,
        item_id=item_id,
        change_type="Remove",
        before_data=before_data,
        after_data=after_data,
        reason=reason
    )
    
    flash(f"Delete request #{change_id} created and awaits admin approval.")

    return redirect("/" + data_type)

@app.route("/<data_type>/delete/<item_ref>/save", methods=["POST"])
def data_item_delete_save(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)

    form_data = request.form.to_dict()
    
    item_id = item["_id"]
    reason = form_data.get("reason", "No Reason Given")
    before_data = item
    after_data = form_data
    
    change_id = request_change(
        data_type=data_type,
        item_id=item_id,
        change_type="Remove",
        before_data=before_data,
        after_data=after_data,
        reason=reason
    )
    
    approve_change(change_id)
    
    flash(f"Delete request #{change_id} created and approved.")

    return redirect("/" + data_type)

#######################################################
#######################################################
#######################################################

@app.route("/login")
def login():
    return discord.create_session(["identify"])

@app.route("/auth/discord/callback")
def callback():
    
    state = request.args.get("state")
    
    try:
        discord.callback()
    except Exception as e:
        print(f"Error during callback: {e}")
        return "Callback error occurred", 400
    
    try:
        discordUser = discord.fetch_user()
        save_user_to_db(discordUser)
        user = mongo.db.players.find_one({"id": str(discordUser.id)})
        session['user'] = {
            'id': user['id'],
            'name': user['name'],
            'avatar_url': user['avatar_url'],
            'is_admin': user['is_admin']
        }
    except Exception as e:
        print(f"Error fetching user: {e}")
        return "Error fetching user information", 400
    
    return redirect(url_for("home"))

@app.route("/refresh")
def refresh_token():
    try:
        discord.refresh_token()
        return redirect(url_for("home"))
    except Exception as e:
        print(f"Error refreshing token: {e}")
        return "Failed to refresh token", 400

@app.route("/refresh_user")
def refresh_user():
    if discord.authorized:
        user = mongo.db.players.find_one({"id": str(discord.fetch_user().id)})
        session['user'] = {
            'id': user['_id'],
            'discordId': user['id'],
            'name': user['name'],
            'avatar_url': user['avatar_url']
        }
    return redirect(url_for("profile"))

@app.route("/logout")
def logout():
    discord.revoke()
    session.clear()
    return redirect(url_for("home"))

@app.route("/profile")
@requires_authorization
def profile():
    if(g.user is None):
        return redirect(url_for("home"))
    return redirect("/players/item/" + g.user["name"])

#Helper function to save user to MongoDB
def save_user_to_db(user):
    db = mongo.db
    
    existing_player = db.players.find_one({"id": str(user.id)})
    if not existing_player:
        db.players.insert_one({
            "id": str(user.id),
            "name": user.name,
            "avatar_url": user.avatar_url,
            "is_admin": False,
            "is_rp_mod": False,
            "is_website_helper": False,
        })
        print(f"Player {user.name} added to database.")
    else:
        db.players.update_one(
            {"id": str(user.id)},
            {"$set": {
                "name": user.name,
                "avatar_url": user.avatar_url
            }}
        )
        print(f"Player {user.name} updated in database.")

##############################################################

def collect_modifiers(target):
    return target.get("modifiers", [])

def collect_laws(target, schema):
    collected_laws = []
    
    schema_properties = schema.get("properties", {})
    
    laws_list = schema.get("laws", [])
    for law_name in laws_list:
        current_law_list = schema_properties.get(law_name, {}).get("laws", {})
        target_law = target.get(law_name, "")
        result = current_law_list.get(target_law, None)
        if result is not None:
            collected_laws.append(result)
    
    return collected_laws

def collect_districts(target, schema):
    district_names = target.get("districts", [])
    
    district_values = []
    
    for name in district_names:
        district_values.append(json_data["districts"].get(name, {}).get("modifiers", {}))
    
    return district_values
    
def collect_jobs_assigned(target, schema):
    return {} #TODO: Implement this

def calculate_job_details(target, modifier_totals, district_totals, law_totals):
    job_details = json_data["jobs"]
    district_details = json_data["districts"]
    
    modifier_sources = [modifier_totals, district_totals, law_totals]
    
    districts = target.get("districts", [])
    
    district_types = []
    
    for district in districts:
        district_types.append(district_details.get(district, {}).get("type", ""))
    
    new_job_details = {}
    
    for job, details in job_details.items():
        if not "requirements" in details or ("district" in details["requirements"] and details["requirements"]["district"] in district_types): #TODO: Account for non-district requirements
            new_details = copy.deepcopy(details)
            for source in modifier_sources:
                for modifier, value in source.items():
                    if modifier.startswith(job):
                        if modifier.endswith("production"):
                            resource = modifier.replace(job + "_", "").replace("_production", "")
                            new_val = new_details.get("production", {}).get(resource, 0) + value
                            if new_val > 0:
                                new_details["production"][resource] = new_val
                            elif resource in new_details["production"]:
                                new_details["production"].pop(resource)
                        elif modifier.endswith("upkeep"):
                            resource = modifier.replace(job + "_", "").replace("_upkeep", "")
                            new_val = new_details.get("upkeep", {}).get(resource, 0) + value
                            if new_val > 0:
                                new_details["upkeep"][resource] = new_val
                            elif resource in new_details["upkeep"]:
                                new_details["upkeep"].pop(resource)
                    elif modifier.startswith("job"):
                        if modifier.endswith("production"):
                            resource = modifier.replace("job_", "").replace("_production", "")
                            new_val = new_details.get("production", {}).get(resource, 0) + value
                            if new_val > 0:
                                new_details["production"][resource] = new_val
                            elif resource in new_details["production"]:
                                new_details["production"].pop(resource)
                        elif modifier.endswith("upkeep"):
                            resource = modifier.replace("job_", "").replace("_upkeep", "")
                            new_val = new_details.get("upkeep", {}).get(resource, 0) + value
                            if new_val > 0:
                                new_details["upkeep"][resource] = new_val
                            elif resource in new_details["upkeep"]:
                                new_details["upkeep"].pop(resource)
            new_job_details[job] = new_details
    
    return new_job_details

##############################################################

def sum_modifier_totals(modifiers):
    modifier_totals = {}
    
    for modifier in modifiers:
        field = modifier["field"]
        modifier_totals[field] = modifier_totals.get(field, 0) + modifier["value"]
    
    return modifier_totals

def sum_law_totals(laws):
    law_totals = {}
    
    for law in laws:
        for target, value in law.items():
            law_totals[target] = law_totals.get(target, 0) + value
    
    return law_totals

def sum_district_totals(districts):
    district_totals = {}
    
    for district in districts:
        for target, value in district.items():
            district_totals[target] = district_totals.get(target, 0) + value
    
    return district_totals    

#jobs_assigned is a dictionary of jobs as keys, and the amount assigned as the value
#job_details is a dictionary with jobs as keys, and the a dictionary of the modifiers that are applied per worker in the job as values
def sum_job_totals(jobs_assigned, job_details):
    job_totals = {}
    
    for job, assigned in jobs_assigned.items():
        job_detail = job_details.get(job, 0)
        for field, value in job_detail.items():
            job_totals[field] = job_totals.get(field, 0) + (value * assigned)
    
    return job_totals    

##############################################################

def calculate_all_fields(target, schema):    
    schema_properties = schema.get("properties", {})
    
    modifiers = collect_modifiers(target)
    modifier_totals = sum_modifier_totals(modifiers)
    
    laws = collect_laws(target, schema)
    law_totals = sum_law_totals(laws)
    
    districts = collect_districts(target, schema)
    district_totals = sum_district_totals(districts)
    
    #TODO: Racial traits and other assorted modifier locations like artifacts and wonders
    
    jobs_assigned = collect_jobs_assigned(target, schema)
    job_details = calculate_job_details(target, modifier_totals, district_totals, law_totals)
    job_totals = sum_job_totals(jobs_assigned, job_details)
    
    calculated_values = {"job_details": job_details}
    
    for field, field_schema in schema_properties.items():
        if isinstance(field_schema, dict) and field_schema.get("calculated"):
            base_value = field_schema.get("base_value", 0)
            calculated_values[field] = compute_field(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals)
            target[field] = calculated_values[field]
    
    return calculated_values

def compute_field(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    modifier_total = modifier_totals.get(field, 0)
    law_total = law_totals.get(field, 0)
    job_total = job_totals.get(field, 0)
    
    compute_func = CUSTOM_COMPUTE_FUNCTIONS.get(field, compute_field_default)
    
    return compute_func(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals)

##############################################################

def compute_field_default(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):

    value = base_value + modifier_totals.get(field, 0) + district_totals.get(field, 0) + law_totals.get(field, 0) + job_totals.get(field, 0)
    
    return value

##############################################################

def compute_field_effective_territory(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    administration = target.get("administration", 0)
    
    value = base_value + modifier_totals.get(field, 0) + district_totals.get(field, 0) + law_totals.get(field, 0) + job_totals.get(field, 0) + (field_schema.get("effective_territory_per_admin", 0) * administration)
    
    return value

def compute_field_road_capacity(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    administration = target.get("administration", 0)
    
    value = base_value + modifier_totals.get(field, 0) + district_totals.get(field, 0) + law_totals.get(field, 0) + job_totals.get(field, 0) + (field_schema.get("road_capacity_per_admin", 0) * administration)
    
    return value
    
def compute_field_karma(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    rolling_karma = int(target.get("rolling_karma", 0))
    temporary_karma = int(target.get("temporary_karma", 0))
    
    value = base_value + modifier_totals.get(field, 0) + district_totals.get(field, 0) + law_totals.get(field, 0) + job_totals.get(field, 0) + rolling_karma + temporary_karma
    
    return value

def compute_disobey_chance(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    compliance = target.get("compliance", "None")
    
    match compliance:
        case "Rebellious":
            return 0.5
        case "Defiant":
            return 0.25
        case "Neutral":
            return 0.15
        case "Compliant":
            return 0.1
    
    return 0

def compute_rebellion_chance(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    compliance = target.get("compliance", "None")
    
    match compliance:
        case "Rebellious":
            return 0.25
        case "Defiant":
            return 0.15
        case "Neutral":
            return 0.05
    
    return 0

def compute_concessions_chance(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    compliance = target.get("compliance", "None")
    
    match compliance:
        case "Rebellious":
            return 0.5
        case "Defiant":
            return 0.4
        case "Neutral":
            return 0.3
        case "Compliant":
            return 0.2
        case "Loyal":
            return 0.1
    
    return 0

def compute_pop_count(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    pop_database = category_data["pops"]["database"]
    target_id = str(target["_id"])
    
    pop_count = pop_database.count_documents({"nation": target_id})
    
    return pop_count

def compute_minority_count(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    pop_database = category_data["pops"]["database"]
    target_id = str(target["_id"])
    
    minority_count = 0
    
    known_cultures = [target.get("primary_culture", "")]
    known_religions = [target.get("primary_religion", "")]
    
    relevant_pops = list(pop_database.find({"nation": target_id}))
    
    for pop in relevant_pops:
        if not pop.get("culture", "") in known_cultures:
            known_cultures.append(pop.get("culture", ""))
            minority_count += 1
        if not pop.get("religion", "") in known_religions:
            known_religions.append(pop.get("religion", ""))
            minority_count += 1
    
    return minority_count

def compute_district_slots(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    pop_count = target.get("pop_count", 0)
    
    value = base_value + modifier_totals.get(field, 0) + district_totals.get(field, 0) + law_totals.get(field, 0) + job_totals.get(field, 0) + math.floor(pop_count / 5)
    
    return value

def compute_unit_capacity(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    pop_count = target.get("pop_count", 0)
    
    unit_cap_from_pops = math.ceil(pop_count * (modifier_totals.get("recruit_percentage", 0) + district_totals.get("recruit_percentage", 0) + law_totals.get("recruit_percentage", 0) + job_totals.get("recruit_percentage", 0)))
    
    value = base_value + modifier_totals.get(field, 0) + district_totals.get(field, 0) + law_totals.get(field, 0) + job_totals.get(field, 0) + unit_cap_from_pops
    
    return value
    
def compute_resource_production(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    production_dict = {}
    
    all_resources = general_resources + unique_resources

    for resource in all_resources:
        specific_resource_production = 0
        modifiers_to_check = [resource["key"] + "_production", "resource_production"]
        for modifier in modifiers_to_check:
            specific_resource_production += modifier_totals.get(modifier, 0) + district_totals.get(modifier, 0) + law_totals.get(modifier, 0) + job_totals.get(modifier, 0)
        production_dict[resource["key"]] = specific_resource_production
    
    return production_dict

def compute_resource_consumption(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    consumption_dict = {}
    
    pop_count = target.get("pop_count", 0)
    
    all_resources = general_resources + unique_resources
    
    for resource in all_resources:
        specific_resource_consumption = 0
        modifiers_to_check = [resource["key"] + "_consumption", "resource_consumption"]
        for modifier in modifiers_to_check:
            specific_resource_consumption += modifier_totals.get(modifier, 0) + district_totals.get(modifier, 0) + law_totals.get(modifier, 0) + job_totals.get(modifier, 0)
        
        if resource["key"] == "food":
            specific_resource_consumption += pop_count
        
        specific_resource_consumption = max(specific_resource_consumption, 0)
        
        consumption_dict[resource["key"]] = specific_resource_consumption
    
    return consumption_dict

def compute_resource_excess(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    excess_dict = {}
    
    production_dict = target.get("resource_production", {})
    consumption_dict = target.get("resource_consumption", {})
    
    all_resources = general_resources + unique_resources
    
    for resource in all_resources:
        excess_dict[resource["key"]] = production_dict.get(resource["key"], 0) - consumption_dict.get(resource["key"], 0)
    
    return excess_dict

def compute_resource_storage_capacity(field, target, base_value, field_schema, modifier_totals, district_totals, law_totals, job_totals):
    storage_dict = {}
    
    all_resources = general_resources + unique_resources
    
    for resource in all_resources:
        specific_resource_storage = resource["base_storage"]
        modifiers_to_check = [resource["key"] + "_storage", "resource_storage"]
        for modifier in modifiers_to_check:
            specific_resource_storage += modifier_totals.get(modifier, 0) + district_totals.get(modifier, 0) + law_totals.get(modifier, 0) + job_totals.get(modifier, 0)
        storage_dict[resource["key"]] = specific_resource_storage
    
    return storage_dict

##############################################################

CUSTOM_COMPUTE_FUNCTIONS = {
    "effective_territory": compute_field_effective_territory,
    "road_capacity": compute_field_road_capacity,
    "karma": compute_field_karma,
    "disobey_chance": compute_disobey_chance,
    "rebellion_chance": compute_rebellion_chance,
    "concessions_chance": compute_concessions_chance,
    "pop_count": compute_pop_count,
    "unique_minority_count": compute_minority_count,
    "district_slots": compute_district_slots,
    "land_unit_capacity": compute_unit_capacity,
    "naval_unit_capacity": compute_unit_capacity,
    "resource_production": compute_resource_production,
    "resource_consumption": compute_resource_consumption,
    "resource_excess": compute_resource_excess,
    "resource_capacity": compute_resource_storage_capacity
}

##############################################################

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
