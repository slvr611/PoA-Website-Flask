from flask import Blueprint, render_template, request, redirect, flash
from helpers.data_helpers import get_data_on_category, get_data_on_item, get_dropdown_options
from helpers.render_helpers import get_linked_objects
from helpers.change_helpers import request_change, approve_change
from helpers.form_helpers import validate_form_with_jsonschema
from calculations.field_calculations import calculate_all_fields
from app_core import category_data, mongo, json_data
from pymongo import ASCENDING
from forms import form_generator

nation_routes = Blueprint("nation_routes", __name__)

@nation_routes.route("/nations/item/<item_ref>")
def nation_item(item_ref):
    """Display a nation's details"""
    schema, db, nation = get_data_on_item("nations", item_ref)
    linked_objects = get_linked_objects(schema, nation)
    calculated_fields = calculate_all_fields(nation, schema, "nation")
    nation.update(calculated_fields)
    
    if "jobs" not in nation:
        nation["jobs"] = {}
    
    return render_template(
        "nation.html",
        title=item_ref,
        schema=schema,
        nation=nation,
        linked_objects=linked_objects,
        general_resources=json_data["general_resources"],
        unique_resources=json_data["unique_resources"],
        districts_config=json_data["districts"],
        cities_config=json_data["cities"]
    )

@nation_routes.route("/nations/edit/<item_ref>", methods=["GET"])
def edit_nation(item_ref):
    """Display nation edit form"""
    schema, db, nation = get_data_on_item("nations", item_ref)
    
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collection"):
            related_collection = attributes.get("collection")
            dropdown_options[field] = list(
                mongo.db[related_collection].find(
                    {}, {"name": 1, "_id": 1}
                ).sort("name", ASCENDING)
            )
    
    linked_objects = get_linked_objects(schema, nation)
    calculated_fields = calculate_all_fields(nation, schema, "nation")
    nation.update(calculated_fields)
    
    form = form_generator.get_form("nations", schema, item=nation)
    form.populate_linked_fields(schema, dropdown_options)

    return render_template(
        "nationEdit.html",
        form=form,
        title="Edit " + item_ref,
        schema=schema,
        nation=nation,
        dropdown_options=dropdown_options,
        linked_objects=linked_objects,
        general_resources=json_data["general_resources"],
        unique_resources=json_data["unique_resources"]
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
def nation_edit_approve(item_ref):
    """Handle nation edit approval"""
    schema, db, nation = get_data_on_item("nations", item_ref)
    
    form = form_generator.get_form("nations", schema, formdata=request.form)
    form.populate_linked_fields(schema, get_dropdown_options(schema))
    
    if not form.validate():
        flash(f"Form validation failed: {form.errors}")
        return redirect("/nations/edit/" + item_ref)
    
    form_data = form.data.copy()
    print(form_data)
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    
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
    return redirect("/nations/item/" + item_ref)
