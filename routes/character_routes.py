from flask import Blueprint, render_template, request, redirect, flash
from helpers.data_helpers import get_data_on_category, get_data_on_item, get_dropdown_options
from helpers.render_helpers import get_linked_objects
from helpers.change_helpers import request_change, approve_change
from helpers.form_helpers import validate_form_with_jsonschema
from calculations.field_calculations import calculate_all_fields
from app_core import category_data, mongo, json_data, character_stats
from helpers.auth_helpers import admin_required
from pymongo import ASCENDING
from forms import form_generator
import random

character_routes = Blueprint("character_routes", __name__)

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

    return render_template(
        "new_character.html",
        form=form,
        title="New Character",
        schema=schema,
        dropdown_options=dropdown_options,
        general_resources=json_data["general_resources"],
        unique_resources=json_data["unique_resources"]
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

    form_data["age"] = 1
    form_data["_id"] = "None"

    for strength in form_data["strengths"]:
        form_data["modifiers"].append({"field": strength, "value": random.randint(1, 2), "duration": -1, "source": "Strength"})
    for weakness in form_data["weaknesses"]:
        form_data["modifiers"].append({"field": weakness, "value": random.randint(-2, -1), "duration": -1, "source": "Weakness"})

    calculated_fields = calculate_all_fields(form_data, schema, "character")

    calculated_character = form_data.copy()
    calculated_character.update(calculated_fields)

    for stat in character_stats:
        if calculated_character[stat] > 4:
            for modifier in form_data["modifiers"]:
                if modifier["field"] == stat:
                    modifier["value"] -= calculated_character[stat] - 4
                    break
    
    random_stats = {}

    while sum(random_stats.values()) < form_data["random_stats"]:
        random_stat = random.choice(character_stats)
        if calculated_character[random_stat] + random_stats.get(random_stat, 0) < 4:
            random_stats[random_stat] = random_stats.get(random_stat, 0) + 1
    
    for stat, value in random_stats.items():
        form_data["modifiers"].append({"field": stat, "value": value, "duration": -1, "source": "Random stats at character creation"})

    form_data["magic_points"] = min(form_data["magic_point_capacity"], form_data["magic_point_income"])

    change_id = request_change(
        data_type="characters",
        item_id=None,
        change_type="Add",
        before_data={},
        after_data=form_data,
        reason=form_data.pop("reason", "No Reason Given")
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

    form_data["age"] = 1
    form_data["_id"] = "None"

    for strength in form_data["strengths"]:
        form_data["modifiers"].append({"field": strength, "value": random.randint(1, 2), "duration": -1, "source": "Strength"})
    for weakness in form_data["weaknesses"]:
        form_data["modifiers"].append({"field": weakness, "value": random.randint(-2, -1), "duration": -1, "source": "Weakness"})
    
    calculated_fields = calculate_all_fields(form_data, schema, "character")

    calculated_character = form_data.copy()
    calculated_character.update(calculated_fields)

    for stat in character_stats:
        if calculated_character[stat] > 4:
            for modifier in form_data["modifiers"]:
                if modifier["field"] == stat:
                    modifier["value"] -= calculated_character[stat] - 4
                    break
    
    random_stats = {}

    while sum(random_stats.values()) < form_data["random_stats"]:
        random_stat = random.choice(character_stats)
        if calculated_character[random_stat] + random_stats.get(random_stat, 0) < 4:
            random_stats[random_stat] = random_stats.get(random_stat, 0) + 1
    
    for stat, value in random_stats.items():
        form_data["modifiers"].append({"field": stat, "value": value, "duration": -1, "source": "Random stats at character creation"})

    form_data["magic_points"] = min(form_data["magic_point_capacity"], form_data["magic_point_income"])

    change_id = request_change(
        data_type="characters",
        item_id=None,
        change_type="Add",
        before_data={},
        after_data=form_data,
        reason=form_data.pop("reason", "No Reason Given")
    )
    
    approve_change(change_id)
    flash(f"Change request #{change_id} created and approved.")
    return redirect("/characters/item/" + form_data["name"])
