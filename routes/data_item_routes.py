from flask import Blueprint, render_template, request, redirect, flash
from forms import form_generator
from helpers.data_helpers import get_data_on_category, get_data_on_item
from helpers.change_helpers import request_change, approve_change
from helpers.render_helpers import get_linked_objects
from helpers.form_helpers import validate_form_with_jsonschema
from routes.nation_routes import edit_nation, nation_edit_request, nation_edit_approve
from calculations.field_calculations import calculate_all_fields
from app_core import category_data, mongo, rarity_rankings, json_data, find_dict_in_list
from helpers.auth_helpers import admin_required
from pymongo import ASCENDING


data_item_routes = Blueprint("data_item_routes", __name__)

@data_item_routes.route("/<data_type>")
def data_list(data_type):
    schema, db = get_data_on_category(data_type)
    query_dict = {"_id": 1, "name": 1}
    preview_overall_lookup_dict = {}

    for preview_item in schema.get("preview", {}):
        query_dict[preview_item] = 1
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
    
    sort_by = schema.get("sort", "name")

    items = list(db.find({}, query_dict).sort("name", ASCENDING))

    if sort_by == "rarity":
        items.sort(key=lambda x: rarity_rankings.get(x.get("rarity", ""), 999))
    else:
        items = list(db.find({}, query_dict).sort(sort_by, ASCENDING))

    return render_template(
        "dataList.html",
        title=category_data[data_type]["pluralName"],
        items=items,
        schema=schema,
        preview_references=preview_overall_lookup_dict
    )

@data_item_routes.route("/<data_type>/item/<item_ref>")
def data_item(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    linked_objects = get_linked_objects(schema, item)
    calculated_fields = calculate_all_fields(item, schema, category_data[data_type]["singularName"].lower())
    item.update(calculated_fields)

    return render_template(
        "dataItem.html",
        title=item.get("name", str(item.get("_id", ""))),
        schema=schema,
        item=item,
        linked_objects=linked_objects,
        json_data=json_data,
        find_dict_in_list=find_dict_in_list
    )

@data_item_routes.route("/<data_type>/edit")
def data_list_edit(data_type):
    schema, db = get_data_on_category(data_type)
    
    query_dict = {"_id": 1, "name": 1}
    preview_overall_lookup_dict = {}
    
    for preview_item in schema.get("preview", {}):
        query_dict[preview_item] = 1
        collection_names = schema.get("properties", {}).get(preview_item, {}).get("collections", None)
        if collection_names:
            preview_individual_lookup_dict = {}
            for collection_name in collection_names:
                preview_db = category_data[collection_name]["database"]
                preview_data = list(preview_db.find({}, {"_id": 1, "name": 1}))
                for data in preview_data:
                    preview_individual_lookup_dict[str(data["_id"])] = {data.get("name", "None"), collection_name + "/item/" + data.get("name", data.get("_id", "#"))}
            preview_overall_lookup_dict[preview_item] = preview_individual_lookup_dict
    
    sort_by = schema.get("sort", "name")

    items = list(db.find({}, query_dict).sort("name", ASCENDING))

    if sort_by == "rarity":
        items.sort(key=lambda x: rarity_rankings.get(x.get("rarity", ""), 999))
    else:
        items = list(db.find({}, query_dict).sort(sort_by, ASCENDING))

    return render_template(
        "dataListEdit.html",
        title=category_data[data_type]["pluralName"],
        items=items,
        schema=schema,
        preview_references=preview_overall_lookup_dict
    )

@data_item_routes.route("/<data_type>/new", methods=["GET"])
def data_item_new(data_type):
    schema, db = get_data_on_category(data_type)
    
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        dropdown_options[field] = []
        if attributes.get("collections") != None:
            related_collections = attributes.get("collections")
            for related_collection in related_collections:
                dropdown_options[field] += list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}).sort("name", ASCENDING))
    
    form = form_generator.get_form(data_type, schema)
    form.populate_linked_fields(schema, dropdown_options)
    
    template = render_template(
        "dataItemNew.html",
        title="New " + category_data[data_type]["singularName"],
        schema=schema,
        form=form,
        dropdown_options=dropdown_options
    )
    
    return template

@data_item_routes.route("/<data_type>/new/request", methods=["POST"])
def data_item_new_request(data_type):
    schema, db = get_data_on_category(data_type)
        
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collections") != None:
            related_collections = attributes.get("collections")
            dropdown_options[field] = []
            for related_collection in related_collections:
                dropdown_options[field] += list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}).sort("name", ASCENDING))
    
    form = form_generator.get_form(data_type, schema, formdata=request.form)
    form.populate_linked_fields(schema, dropdown_options)
    
    if not form.validate():
        flash("Form validation failed!")
        return redirect("/" + data_type + "/new")
    
    # Get form data
    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    
    # Additional JSON schema validation
    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect("/" + data_type + "/new")
    
    if "name" in form_data and db.find_one({"name": form_data["name"]}):
        flash("Name must be unique!")
        return redirect("/" + data_type + "/new")
    
    reason = form_data.pop("reason", "No Reason Given")
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

@data_item_routes.route("/<data_type>/new/save", methods=["POST"])
@admin_required
def data_item_new_approve(data_type):
    schema, db = get_data_on_category(data_type)
        
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collections") != None:
            related_collections = attributes.get("collections")
            dropdown_options[field] = []
            for related_collection in related_collections:
                dropdown_options[field] += list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}).sort("name", ASCENDING))
    
    form = form_generator.get_form(data_type, schema, formdata=request.form)
    form.populate_linked_fields(schema, dropdown_options)
    
    if not form.validate():
        flash("Form validation failed!")
        return redirect("/" + data_type + "/new")
    
    # Get form data
    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    
    # Additional JSON schema validation
    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect("/" + data_type + "/new")
    
    if "name" in form_data and db.find_one({"name": form_data["name"]}):
        flash("Name must be unique!")
        return redirect("/" + data_type + "/new")
    
    reason = form_data.pop("reason", "No Reason Given")
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

@data_item_routes.route("/<data_type>/edit/<item_ref>", methods=["GET"])
def data_item_edit(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    
    dropdown_options = {}
    for field, attrs in schema["properties"].items():
        if attrs.get("collections"):
            related_collections = attrs.get("collections")
            dropdown_options[field] = []
            for related_collection in related_collections:
                dropdown_options[field] += list(
                    mongo.db[related_collection].find(
                        {}, {"name": 1, "_id": 1}
                    ).sort("name", ASCENDING)
                )

    calculated_fields = calculate_all_fields(item, schema, category_data[data_type]["singularName"].lower())
    item.update(calculated_fields)

    form = form_generator.get_form(data_type, schema, item=item)
    form.populate_linked_fields(schema, dropdown_options)
    
    return render_template(
        "dataItemEdit.html",
        title=f"Edit {item_ref}",
        schema=schema,
        form=form,
        item=item,
        dropdown_options=dropdown_options
    )

@data_item_routes.route("/<data_type>/edit/<item_ref>/request", methods=["POST"])
def data_item_edit_request(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    
    if data_type == "nations": #This should never happen, but just a good fallback
        print("Had to redirect from data_item_routes to nation_routes")
        return nation_edit_request(item_ref)
    
    form = form_generator.get_form(data_type, schema, formdata=request.form)
    
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collections") != None:
            related_collections = attributes.get("collections")
            dropdown_options[field] = []
            for related_collection in related_collections:
                dropdown_options[field] += list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}).sort("name", ASCENDING))
    
    form.populate_linked_fields(schema, dropdown_options)
    
    if not form.validate():
        flash("Form validation failed!")
        flash(form.errors)
        return redirect(f"/{data_type}/edit/{item_ref}")
    
    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    
    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
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
    
    return redirect("/" + data_type)

@data_item_routes.route("/<data_type>/edit/<item_ref>/save", methods=["POST"])
@admin_required
def data_item_edit_approve(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    
    if data_type == "nations": #This should never happen, but just a good fallback
        print("Had to redirect from data_item_routes to nation_routes")
        return nation_edit_request(item_ref)
    
    form = form_generator.get_form(data_type, schema, formdata=request.form)
    
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collections") != None:
            related_collections = attributes.get("collections")
            dropdown_options[field] = []
            for related_collection in related_collections:
                dropdown_options[field] += list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}).sort("name", ASCENDING))
    
    form.populate_linked_fields(schema, dropdown_options)
    
    if not form.validate():
        flash("Form validation failed!")
        flash(form.errors)
        return redirect("/" + data_type + "/edit/" + item_ref)
    
    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    
    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
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

@data_item_routes.route("/<data_type>/clone/<item_ref>/request", methods=["POST"])
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

@data_item_routes.route("/<data_type>/clone/<item_ref>/save", methods=["POST"])
@admin_required
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

@data_item_routes.route("/<data_type>/delete/<item_ref>/request", methods=["POST"])
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

@data_item_routes.route("/<data_type>/delete/<item_ref>/save", methods=["POST"])
@admin_required
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

@data_item_routes.route("/wonders")
def wonder_list():
    schema, db = get_data_on_category("wonders")
    query_dict = {"_id": 1, "name": 1}
    preview_overall_lookup_dict = {}

    for preview_item in schema.get("preview", {}):
        query_dict[preview_item] = 1
        collection_names = schema.get("properties", {}).get(preview_item, {}).get("collections", None)
        if collection_names:
            for collection_name in collection_names:
                preview_db = category_data[collection_name]["database"]
                preview_individual_lookup_dict = {}
                preview_data = list(preview_db.find({}, {"_id": 1, "name": 1}))
                for data in preview_data:
                    preview_individual_lookup_dict[str(data["_id"])] = {
                        "name": data.get("name", "None"),
                    "link": f"{collection_name}/item/{data.get('name', data.get('_id', '#'))}"
                }
            preview_overall_lookup_dict[preview_item] = preview_individual_lookup_dict
    
    sort_by = schema.get("sort", "name")

    items = list(db.find({}, query_dict).sort("name", ASCENDING))

    if sort_by == "rarity":
        items.sort(key=lambda x: rarity_rankings.get(x.get("rarity", ""), 999))
    else:
        items = list(db.find({}, query_dict).sort(sort_by, ASCENDING))

    return render_template(
        "wonder_list.html",
        title=category_data["wonders"]["pluralName"],
        items=items,
        schema=schema,
        preview_references=preview_overall_lookup_dict
    )

@data_item_routes.route("/races")
def races_list():
    schema = category_data["races"]["schema"]
    db = category_data["races"]["database"]
    
    # Get all races
    races = list(db.find().sort("name", ASCENDING))
    
    # Get all pops and count by race
    pops = list(mongo.db.pops.find({}, {"race": 1}))
    race_counts = {}
    
    # Create a lookup dictionary for race names
    race_id_to_name = {}
    for race in races:
        race_id_to_name[str(race["_id"])] = race["name"]
    
    # Count pops by race
    for pop in pops:
        race_id = pop.get("race")
        if race_id:
            race_name = race_id_to_name.get(race_id, "Unknown")
            race_counts[race_name] = race_counts.get(race_name, 0) + 1
    
    # Sort by count (descending)
    sorted_race_data = sorted(race_counts.items(), key=lambda x: x[1], reverse=True)
    
    # Prepare data for chart
    race_labels = [item[0] for item in sorted_race_data]
    race_values = [item[1] for item in sorted_race_data]
    
    # Create a dictionary of pop counts by race name for the table
    pop_counts = {}
    for race in races:
        race_name = race["name"]
        pop_counts[race_name] = race_counts.get(race_name, 0)
    
    return render_template(
        "race_list.html",
        title=category_data["races"]["pluralName"],
        items=races,
        schema=schema,
        race_labels=race_labels,
        race_values=race_values,
        pop_counts=pop_counts
    )

@data_item_routes.route("/cultures")
def cultures_list():
    schema = category_data["cultures"]["schema"]
    db = category_data["cultures"]["database"]
    
    # Get all cultures
    cultures = list(db.find().sort("name", ASCENDING))
    
    # Get all pops and count by culture
    pops = list(mongo.db.pops.find({}, {"culture": 1}))
    culture_counts = {}
    
    # Create a lookup dictionary for culture names
    culture_id_to_name = {}
    for culture in cultures:
        culture_id_to_name[str(culture["_id"])] = culture["name"]
    
    # Count pops by culture
    for pop in pops:
        culture_id = pop.get("culture")
        if culture_id:
            culture_name = culture_id_to_name.get(culture_id, "Unknown")
            culture_counts[culture_name] = culture_counts.get(culture_name, 0) + 1
    
    # Sort by count (descending)
    sorted_culture_data = sorted(culture_counts.items(), key=lambda x: x[1], reverse=True)
    
    # Prepare data for chart
    culture_labels = [item[0] for item in sorted_culture_data]
    culture_values = [item[1] for item in sorted_culture_data]
    
    # Create a dictionary of pop counts by culture name for the table
    pop_counts = {}
    for culture in cultures:
        culture_name = culture["name"]
        pop_counts[culture_name] = culture_counts.get(culture_name, 0)
    
    return render_template(
        "culture_list.html",
        title=category_data["cultures"]["pluralName"],
        items=cultures,
        schema=schema,
        culture_labels=culture_labels,
        culture_values=culture_values,
        pop_counts=pop_counts
    )

@data_item_routes.route("/religions")
def religions_list():
    schema = category_data["religions"]["schema"]
    db = category_data["religions"]["database"]
    
    # Get all religions
    religions = list(db.find().sort("name", ASCENDING))
    
    # Get all pops and count by religion
    pops = list(mongo.db.pops.find({}, {"religion": 1}))
    religion_counts = {}
    
    # Create a lookup dictionary for religion names
    religion_id_to_name = {}
    for religion in religions:
        religion_id_to_name[str(religion["_id"])] = religion["name"]
    
    # Count pops by religion
    for pop in pops:
        religion_id = pop.get("religion")
        if religion_id:
            religion_name = religion_id_to_name.get(religion_id, "Unknown")
            religion_counts[religion_name] = religion_counts.get(religion_name, 0) + 1
    
    # Sort by count (descending)
    sorted_religion_data = sorted(religion_counts.items(), key=lambda x: x[1], reverse=True)
    
    # Prepare data for chart
    religion_labels = [item[0] for item in sorted_religion_data]
    religion_values = [item[1] for item in sorted_religion_data]
    
    # Create a dictionary of pop counts by religion name for the table
    pop_counts = {}
    for religion in religions:
        religion_name = religion["name"]
        pop_counts[religion_name] = religion_counts.get(religion_name, 0)
    
    return render_template(
        "religion_list.html",
        title=category_data["religions"]["pluralName"],
        items=religions,
        schema=schema,
        religion_labels=religion_labels,
        religion_values=religion_values,
        pop_counts=pop_counts
    )
