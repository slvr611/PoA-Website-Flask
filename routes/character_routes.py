from flask import Blueprint, render_template, request, redirect, flash
from helpers.data_helpers import get_data_on_category, get_data_on_item, get_dropdown_options
from helpers.render_helpers import get_linked_objects
from helpers.change_helpers import request_change, approve_change
from helpers.form_helpers import validate_form_with_jsonschema
from calculations.field_calculations import calculate_all_fields
from app_core import category_data, mongo, json_data, character_stats
from helpers.auth_helpers import admin_required
from helpers.tick_helpers import RULER_TYPE_STATS
from pymongo import ASCENDING
from forms import form_generator
import random

def validate_character_strengths_weaknesses(form_data):
    """Returns an error string or None if valid."""
    strengths = form_data.get("strengths", [])
    weaknesses = form_data.get("weaknesses", [])
    if len(strengths) != 2:
        return "Character must have exactly 2 strengths."
    if len(weaknesses) != 2:
        return "Character must have exactly 2 weaknesses."
    if set(strengths) & set(weaknesses):
        return "A stat cannot be both a strength and a weakness."
    character_type = form_data.get("character_type", "")
    ruler = RULER_TYPE_STATS.get(character_type)
    if ruler:
        if ruler["strength"] not in strengths:
            return f"{character_type} must have {ruler['strength'].capitalize()} as a strength."
        if ruler["weakness"] not in weaknesses:
            return f"{character_type} must have {ruler['weakness'].capitalize()} as a weakness."
    return None

character_routes = Blueprint("character_routes", __name__)

@character_routes.route("/characters", methods=["GET"])
def character_list():
    schema, db = get_data_on_category("characters")
    
    # Build preview references lookup like dataList does
    preview_overall_lookup_dict = {}
    for preview_item in schema.get("preview", {}):
        collection_names = schema.get("properties", {}).get(preview_item, {}).get("collections", None)
        if collection_names:
            preview_individual_lookup_dict = {}
            for collection_name in collection_names:
                preview_db = category_data[collection_name]["database"]
                preview_data = list(preview_db.find({}, {"_id": 1, "name": 1}))
                for data in preview_data:
                    preview_individual_lookup_dict[str(data["_id"])] = {
                        "name": data.get("name", "None"),
                        "link": f"{collection_name}/item/{data.get('name', data.get('_id', '#'))}"
                    }
            preview_overall_lookup_dict[preview_item] = preview_individual_lookup_dict
    
    # Get all characters
    all_characters = list(db.find().sort("name", ASCENDING))
    
    # Separate into player, AI, and dead characters
    player_characters = []
    ai_characters = []
    dead_characters = []

    for character in all_characters:
        if character.get("health_status") == "Dead":
            dead_characters.append(character)
        elif character.get("player"):
            player_characters.append(character)
        else:
            ai_characters.append(character)

    return render_template(
        "character_list.html",
        title="Characters",
        player_characters=player_characters,
        ai_characters=ai_characters,
        dead_characters=dead_characters,
        schema=schema,
        preview_references=preview_overall_lookup_dict
    )

@character_routes.route("/characters/new", methods=["GET"])
def new_character():
    """Display new character form"""
    schema, db = get_data_on_category("characters")
    
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collections"):
            related_collections = attributes.get("collections")
            dropdown_options[field] = []
            for related_collection in related_collections:
                dropdown_options[field] += list(
                    mongo.db[related_collection].find(
                        {}, {"name": 1, "_id": 1}
                    ).sort("name", ASCENDING)
                )
    
    form = form_generator.get_form("new_character", schema)
    form.populate_linked_fields(schema, dropdown_options)

    predecessor_characters = list(db.find({}).sort("name", ASCENDING))

    return render_template(
        "new_character.html",
        form=form,
        title="New Character",
        schema=schema,
        dropdown_options=dropdown_options,
        general_resources=json_data["general_resources"],
        unique_resources=json_data["unique_resources"],
        entity_source_type="character",
        predecessor_characters=predecessor_characters,
    )

@character_routes.route("/characters/new/request", methods=["POST"])
def new_character_request():
    """Handle new character request"""
    schema, db = get_data_on_category("characters")
    
    form = form_generator.get_form("new_character", schema, formdata=request.form)
    form.populate_linked_fields(schema, get_dropdown_options(schema))
    
    if not form.validate():
        flash(f"Form validation failed: {form.errors}")
        return redirect("/characters/new")
    
    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    if "name" in form_data:
        form_data["name"] = form_data.get("name", "").strip()
    
    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect("/characters/new")
    
    if db.find_one({"name": form_data["name"]}):
        flash("Name must be unique!")
        return redirect("/characters/new")

    error = validate_character_strengths_weaknesses(form_data)
    if error:
        flash(f"Validation Error: {error}")
        return redirect("/characters/new")

    form_data["age"] = 1
    form_data["_id"] = "None"

    for strength in form_data["strengths"]:
        form_data["modifiers"].append({"modifier_type": "attribute", "attribute": strength, "value": random.randint(2, 4), "duration": -1, "source": "Strength"})
    for weakness in form_data["weaknesses"]:
        form_data["modifiers"].append({"modifier_type": "attribute", "attribute": weakness, "value": random.randint(-4, -2), "duration": -1, "source": "Weakness"})

    calculated_fields = calculate_all_fields(form_data, schema, "character")

    calculated_character = form_data.copy()
    calculated_character.update(calculated_fields)

    for stat in character_stats:
        stat_cap = calculated_character.get(stat + "_cap", 4)
        if calculated_character[stat] > stat_cap:
            for modifier in form_data["modifiers"]:
                if modifier.get("attribute") == stat or modifier.get("field") == stat:
                    modifier["value"] -= calculated_character[stat] - stat_cap
                    break

    random_stats = {}

    while sum(random_stats.values()) < form_data["random_stats"]:
        random_stat = random.choice(character_stats)
        stat_cap = calculated_character.get(random_stat + "_cap", 4)
        if calculated_character[random_stat] + random_stats.get(random_stat, 0) < stat_cap:
            random_stats[random_stat] = random_stats.get(random_stat, 0) + 1

    for stat, value in random_stats.items():
        form_data["modifiers"].append({"modifier_type": "attribute", "attribute": stat, "value": value, "duration": -1, "source": "Random stats at character creation"})

    calculated_fields = calculate_all_fields(calculated_character, schema, "character")

    calculated_character.update(calculated_fields)


    form_data["magic_points"] = min(calculated_character["magic_point_capacity"], calculated_character["magic_point_income"])

    predecessor_id = request.form.get("predecessor_id", "").strip()
    reason = form_data.pop("reason", "No Reason Given")
    if predecessor_id:
        reason += f" [Successor to character ID: {predecessor_id}]"

    change_id = request_change(
        data_type="characters",
        item_id=None,
        change_type="Add",
        before_data={},
        after_data=form_data,
        reason=reason,
    )

    flash(f"Change request #{change_id} created and awaits admin approval.")
    return redirect("/characters")

@character_routes.route("/characters/new/save", methods=["POST"])
@admin_required
def new_character_approve():
    """Handle new character approval (admin only)"""
    schema, db = get_data_on_category("characters")
    
    form = form_generator.get_form("new_character", schema, formdata=request.form)
    form.populate_linked_fields(schema, get_dropdown_options(schema))
    
    if not form.validate():
        flash(f"Form validation failed: {form.errors}")
        return redirect("/characters/new")
    
    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    if "name" in form_data:
        form_data["name"] = form_data.get("name", "").strip()
    
    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect("/characters/new")
    
    if db.find_one({"name": form_data["name"]}):
        flash("Name must be unique!")
        return redirect("/characters/new")

    error = validate_character_strengths_weaknesses(form_data)
    if error:
        flash(f"Validation Error: {error}")
        return redirect("/characters/new")

    form_data["age"] = 1
    form_data["_id"] = "None"

    for strength in form_data["strengths"]:
        form_data["modifiers"].append({"modifier_type": "attribute", "attribute": strength, "value": random.randint(2, 4), "duration": -1, "source": "Strength"})
    for weakness in form_data["weaknesses"]:
        form_data["modifiers"].append({"modifier_type": "attribute", "attribute": weakness, "value": random.randint(-4, -2), "duration": -1, "source": "Weakness"})

    calculated_fields = calculate_all_fields(form_data, schema, "character")

    calculated_character = form_data.copy()
    calculated_character.update(calculated_fields)

    for stat in character_stats:
        stat_cap = calculated_character.get(stat + "_cap", 4)
        if calculated_character[stat] > stat_cap:
            for modifier in form_data["modifiers"]:
                if modifier.get("attribute") == stat or modifier.get("field") == stat:
                    modifier["value"] -= calculated_character[stat] - stat_cap
                    break

    random_stats = {}

    while sum(random_stats.values()) < form_data["random_stats"]:
        random_stat = random.choice(character_stats)
        stat_cap = calculated_character.get(random_stat + "_cap", 4)
        if calculated_character[random_stat] + random_stats.get(random_stat, 0) < stat_cap:
            random_stats[random_stat] = random_stats.get(random_stat, 0) + 1

    for stat, value in random_stats.items():
        form_data["modifiers"].append({"modifier_type": "attribute", "attribute": stat, "value": value, "duration": -1, "source": "Random stats at character creation"})

    form_data["magic_points"] = min(form_data.get("magic_point_capacity", 0), form_data.get("magic_point_income", 0))

    predecessor_id = request.form.get("predecessor_id", "").strip()

    change_id = request_change(
        data_type="characters",
        item_id=None,
        change_type="Add",
        before_data={},
        after_data=form_data,
        reason=form_data.pop("reason", "No Reason Given"),
    )

    approve_change(change_id)

    if predecessor_id:
        new_char = db.find_one({"name": form_data["name"]})
        if new_char:
            mongo.db.artifacts.update_many(
                {"owner": predecessor_id},
                {"$set": {"owner": str(new_char["_id"])}}
            )

    flash(f"Change request #{change_id} created and approved.")
    return redirect("/characters/item/" + form_data["name"])
