from flask import Flask, redirect, url_for, session, request, render_template, g, flash, abort
from flask_discord import DiscordOAuth2Session, requires_authorization, Unauthorized
from flask_pymongo import PyMongo
from dotenv import load_dotenv
from jsonschema import validate, ValidationError
from bson import ObjectId
from datetime import datetime
import os
import json

# Load environment variables from .env file
load_dotenv()

# Initialize the Flask app
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey")

# Discord OAuth configuration
app.config["DISCORD_CLIENT_ID"] = os.getenv("DISCORD_CLIENT_ID")
app.config["DISCORD_CLIENT_SECRET"] = os.getenv("DISCORD_CLIENT_SECRET")
app.config["DISCORD_REDIRECT_URI"] = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:5000/auth/discord/callback")

discord = DiscordOAuth2Session(app)

# MongoDB configuration
app.config["MONGO_URI"] = os.getenv("MONGO_URI", "mongodb://localhost:27017/flask_discord_app")
mongo = PyMongo(app)

# Constants
navbarPages = ["nations", "regions", "races", "cultures", "religions", "merchants", "mercenaries", "characters", "artifacts", "spells", "wonders", "markets", "wars", "changes"]

category_data = {
    "nations": {"pluralName": "Nations", "singularName": "Nation", "database": mongo.db.nations},
    "regions": {"pluralName": "Regions", "singularName": "Region", "database": mongo.db.regions},
    "races": {"pluralName": "Races", "singularName": "Race", "database": mongo.db.races},
    "cultures": {"pluralName": "Cultures", "singularName": "Culture", "database": mongo.db.cultures},
    "religions": {"pluralName": "Religions", "singularName": "Religion", "database": mongo.db.religions},
    "merchants": {"pluralName": "Merchants", "singularName": "Merchant", "database": mongo.db.merchants},
    "mercenaries": {"pluralName": "Mercenaries", "singularName": "Mercenary", "database": mongo.db.mercenaries},
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

def load_schema(file_path):
    with open(file_path, "r") as file:
        schema = json.load(file)
    return schema["$jsonSchema"]

for data_type in category_data:
    category_data[data_type]["schema"] = load_schema("schemas/" + data_type + ".json")

@app.context_processor
def inject_navbar_pages():
    return {'navbarPages': navbarPages, 'category_data': category_data}

# Middleware
@app.before_request
def load_user():
    g.user = session.get('user', None)

# Routes
@app.route("/")
def home():
    return render_template("index.html")

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
        # Not a valid ObjectId, fallback to name
        item = db.find_one({"name": item_ref})
    
    if not item:
        abort(404, "Item not found")
    
    return schema, db, item

#######################################################

@app.route("/<data_type>")
def data_list(data_type):
    schema, db = get_data_on_category(data_type)
    
    items = list(db.find())

    return render_template(
        "dataList.html",
        title=category_data[data_type]["pluralName"],
        items=items
    )

#######################################################

@app.route("/<data_type>/edit")
def data_list_edit(data_type):
    schema, db = get_data_on_category(data_type)
    
    items = list(db.find())

    return render_template(
        "dataListEdit.html",
        title=category_data[data_type]["pluralName"],
        items=items
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
    
    db.insert_one(form_data) #Replace with a call to the request change function
    flash(data_type + " created successfully!")
    
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
    
    db.insert_one(form_data) #Replace with a call to the request change function and approve change function
    flash(data_type + " created successfully!")
    
    return redirect("/" + data_type)

#######################################################

@app.route("/<data_type>/<item_ref>")
def data_item(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    
    linked_objects = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collection") != None:
            related_collection = attributes.get("collection")
            
            if attributes.get("queryTargetAttribute") != None:
                query_target = attributes.get("queryTargetAttribute")
                item_id = str(item["_id"])

                related_items = list(mongo.db[related_collection].find({query_target: item_id}, {"name": 1, "_id": 1}))
                
                if related_items:
                    linked_objects[field] = []
                    for obj in related_items:
                        if "name" in obj:
                            linked_objects[field].append({"name": obj["name"], "link": f"/{related_collection}/{obj['name']}"})
                        else:
                            linked_objects[field].append({"name": obj["_id"], "link": f"/{related_collection}/{obj['_id']}"})
                        
            
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
                        linked_object["link"] = "/" + related_collection + "/" + linked_object["name"]
                    
                    linked_objects[field] = linked_object
    
    return render_template(
        "dataItem.html",
        title=item_ref,
        schema=schema,
        item=item,
        linked_objects=linked_objects
    )

#######################################################

@app.route("/<data_type>/<item_ref>/edit", methods=["GET"])
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



@app.route("/<data_type>/<item_ref>/edit/request", methods=["POST"])
def data_item_edit_request(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    
    form_data = request.form.to_dict()
    
    try:
        validate(instance=form_data, schema=schema)
    except ValidationError as e:
        flash(f"Validation Error: {e.message}")
        return redirect(f"/{data_type}/{item_ref}/edit")
    
    if "name" in form_data and form_data["name"] != item_ref and db.find_one({"name": form_data["name"]}):
        flash("Name must be unique!")
        return redirect(f"/{data_type}/{item_ref}/edit")
    
    item_id = item["_id"]
    reason = form_data.get("reason", "No Reason Given")
    before_data = item
    after_data = form_data
    
    requester = g.user["id"]
    
    change_id = request_change(
        data_type=data_type,
        item_id=item_id,
        before_data=before_data,
        after_data=after_data,
        reason=reason,
        requester=requester
    )
    
    flash(f"Change request #{change_id} created and awaits admin approval.")
    
    return redirect(f"/{data_type}")



@app.route("/<data_type>/<item_ref>/edit/save", methods=["POST"])
def data_item_edit_approve(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    
    form_data = request.form.to_dict()
    
    try:
        validate(instance=form_data, schema=schema)
    except ValidationError as e:
        flash(f"Validation Error: {e.message}")
        return redirect("/" + data_type + "/" + item_ref + "/edit")
    
    if "name" in form_data and form_data["name"] != item_ref and db.find_one({"name": form_data["name"]}):
        flash("Name must be unique!")
        return redirect(f"/{data_type}/{item_ref}/edit")
    
    item_id = item["_id"]
    reason = form_data.get("reason", "No Reason Given")
    before_data = item
    after_data = form_data
    
    requester = g.user["id"]
    
    change_id = request_change(
        data_type=data_type,
        item_id=item_id,
        before_data=before_data,
        after_data=after_data,
        reason=reason,
        requester=requester
    )
    
    flash(f"Change request #{change_id} created and awaits admin approval.")
    
    return redirect("/" + data_type)

#######################################################

# Helper function to request a change
def request_change(data_type, item_id, before_data, after_data, reason, requester):
    changes_coll = mongo.db.changes
    now = datetime.utcnow()
    
    differential = compute_differential(before_data, after_data)
    
    change_doc = {
        "target_collection": data_type,
        "target_id": item_id,
        "time_requested": now,
        "requester": requester,
        "before_requested_data": before_data,
        "after_requested_data": after_data,
        "differential_data": differential,
        "before_implemented_data": None,
        "after_change_data": None,
        "request_reason": reason,
        "status": "Pending"
    }
    
    result = changes_coll.insert_one(change_doc)
    return result.inserted_id

def approve_change(data_type, item_name):
    return

def compute_differential(before_data, after_data):
    diff = {}
    for key in set(before_data.keys()) | set(after_data.keys()):
        before_val = before_data.get(key)
        after_val = after_data.get(key)
        if isinstance(before_val, int) and isinstance(after_val, int):
            diff[key] = {after_val - before_val}
    return diff

#######################################################

@app.route("/<data_type>/<item_ref>/delete")
def data_item_delete(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    
    if "name" in item:
        db.delete_one({"name": item_ref})
        flash(f"Item named #{item_ref} deleted.")
    else:
        obj_id = ObjectId(item_ref)
        db.delete_one({"_id": obj_id})
        flash(f"Item with ID #{item_ref} deleted.")

    # Pass data to the template
    return redirect("/" + data_type)

#######################################################
#######################################################
#######################################################

@app.route("/login")
def login():
    return discord.create_session(["identify"])

@app.route("/auth/discord/callback")
def callback():
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
            'isAdmin': user['isAdmin']
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
    return render_template("profile.html")

# Helper function to save user to MongoDB
def save_user_to_db(user):
    db = mongo.db
    
    existing_player = db.players.find_one({"id": str(user.id)})
    if not existing_player:
        db.players.insert_one({
            "id": str(user.id),
            "name": user.name,
            "avatar_url": user.avatar_url,
            "isAdmin": False
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

if __name__ == "__main__":
    app.run(debug=True)
