from flask import Blueprint, render_template, request, redirect, flash, g
from helpers.data_helpers import get_data_on_category, get_data_on_item, get_dropdown_options
from helpers.render_helpers import get_linked_objects
from helpers.change_helpers import request_change, approve_change, system_approve_change
from helpers.form_helpers import validate_form_with_jsonschema
from helpers.auth_helpers import owner_required
from calculations.field_calculations import calculate_all_fields
from app_core import category_data, mongo, json_data
from helpers.auth_helpers import admin_required
from pymongo import ASCENDING
from forms import form_generator, wtform_to_json
import json

nation_routes = Blueprint("nation_routes", __name__)



@nation_routes.route("/nations/item/<item_ref>")
def nation_item(item_ref):
    """Display a nation's details"""
    schema, db, nation = get_data_on_item("nations", item_ref)
    linked_objects = get_linked_objects(schema, nation)
    calculated_fields = calculate_all_fields(nation, schema, "nation")
    nation.update(calculated_fields)

    user_is_owner = False
    if g.user:
        user = mongo.db.players.find_one({"id": g.user.get("id")})
        if user:
            user_characters = list(mongo.db.characters.find({"player": str(user["_id"])}))
            for character in user_characters:
                if str(character.get("ruling_nation_org", "")) == str(nation["_id"]):
                    user_is_owner = True
                    break
    
    if "jobs" not in nation:
        nation["jobs"] = {}
    
    return render_template(
        "nation_owner.html",
        title=item_ref,
        schema=schema,
        nation=nation,
        linked_objects=linked_objects,
        json_data=json_data,
        cities_config=json_data["cities"],
        user_is_owner=user_is_owner
    )

@nation_routes.route("/nations/edit/<item_ref>", methods=["GET"])
def edit_nation(item_ref):
    """Display nation edit form"""
    schema, db, nation = get_data_on_item("nations", item_ref)
    
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
    
    linked_objects = get_linked_objects(schema, nation)
    calculated_fields = calculate_all_fields(nation, schema, "nation")
    nation.update(calculated_fields)

    # Ensure concessions is properly formatted
    if "concessions" in nation and nation["concessions"] is not None:
        if not isinstance(nation["concessions"], dict):
            nation["concessions"] = {}
    else:
        nation["concessions"] = {}
    
    form = form_generator.get_form("nations", schema, item=nation)
    form.populate_linked_fields(schema, dropdown_options)

    # Set concessions as JSON string
    form.concessions.data = json.dumps(nation.get("concessions", {}))

    return render_template(
        "nation_owner_edit.html",
        form=form,
        form_json=wtform_to_json(form),
        title="Edit " + item_ref,
        schema=schema,
        nation=nation,
        dropdown_options=dropdown_options,
        linked_objects=linked_objects,
        json_data=json_data
    )

@nation_routes.route("/nations/edit/<item_ref>/request", methods=["POST"])
def nation_edit_request(item_ref):
    """Handle nation edit request"""
    schema, db, nation = get_data_on_item("nations", item_ref)

    form = form_generator.get_form("nations", schema, formdata=request.form)
    form.populate_linked_fields(schema, get_dropdown_options(schema))
    
    if not form.validate():
        flash(f"Form validation failed: {form.errors}")
        return redirect("/nations/edit/" + item_ref)
    
    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    
    # Process concessions field
    if 'concessions' in form_data:
        try:
            if isinstance(form_data['concessions'], str):
                form_data['concessions'] = json.loads(form_data['concessions'])
        except (json.JSONDecodeError, TypeError):
            form_data['concessions'] = {}
    
    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect("/nations/edit/" + item_ref)
    
    if form_data["name"] != item_ref and db.find_one({"name": form_data["name"]}):
        flash("Name must be unique!")
        return redirect(f"/nations/edit/{item_ref}")
    
    change_id = request_change(
        data_type="nations",
        item_id=nation["_id"],
        change_type="Update",
        before_data=nation,
        after_data=form_data,
        reason=form_data.pop("reason", "No Reason Given")
    )
    
    flash(f"Change request #{change_id} created and awaits admin approval.")
    return redirect("/nations/item/" + item_ref)

@nation_routes.route("/nations/edit/<item_ref>/save", methods=["POST"])
@admin_required
def nation_edit_approve(item_ref):
    """Handle nation edit approval"""
    schema, db, nation = get_data_on_item("nations", item_ref)
    
    form = form_generator.get_form("nations", schema, formdata=request.form)
    form.populate_linked_fields(schema, get_dropdown_options(schema))
    
    if not form.validate():
        flash(f"Form validation failed: {form.errors}")
        return redirect("/nations/edit/" + item_ref)
    
    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    
    # Process concessions field
    if 'concessions' in form_data:
        try:
            if isinstance(form_data['concessions'], str):
                form_data['concessions'] = json.loads(form_data['concessions'])
        except (json.JSONDecodeError, TypeError):
            form_data['concessions'] = {}

    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect("/nations/edit/" + item_ref)
    
    if form_data["name"] != item_ref and db.find_one({"name": form_data["name"]}):
        flash("Name must be unique!")
        return redirect(f"/nations/edit/{item_ref}")
    
    change_id = request_change(
        data_type="nations",
        item_id=nation["_id"],
        change_type="Update",
        before_data=nation,
        after_data=form_data,
        reason=form_data.pop("reason", "No Reason Given")
    )
    
    approve_change(change_id)
    flash(f"Change request #{change_id} created and approved.")
    return redirect("/nations/item/" + form_data["name"])

@nation_routes.route("/nations/edit_jobs/<item_ref>", methods=["GET"])
@owner_required("nations")
def edit_nation_jobs(item_ref):
    """Display nation job edit form"""
    schema, db, nation = get_data_on_item("nations", item_ref)
        
    calculated_fields = calculate_all_fields(nation, schema, "nation_jobs")
    nation.update(calculated_fields)
    
    form = form_generator.get_form("jobs", schema, item=nation)

    return render_template(
        "nation_jobs_edit.html",
        form=form,
        title="Edit Jobs for " + item_ref,
        schema=schema,
        nation=nation
    )

@nation_routes.route("/nations/edit_jobs/<item_ref>/save", methods=["POST"])
@owner_required("nations")
def nation_edit_jobs_approve(item_ref):
    """Handle nation jobs edit approval"""
    schema, db, nation = get_data_on_item("nations", item_ref)
    
    form = form_generator.get_form("jobs", schema, formdata=request.form)
    
    if not form.validate():
        flash(f"Form validation failed: {form.errors}")
        return redirect("/nations/edit_jobs/" + item_ref)
    
    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    
    change_id = request_change(
        data_type="nations",
        item_id=nation["_id"],
        change_type="Update",
        before_data=nation,
        after_data=form_data,
        reason="Job Assignment"
    )
    
    print(system_approve_change(change_id))
    flash(f"Change request #{change_id} created and approved.")
    return redirect("/nations/item/" + item_ref)
