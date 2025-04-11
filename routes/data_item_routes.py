from flask import Blueprint, render_template, request, redirect, flash
from forms import form_generator
from helpers.data_helpers import get_data_on_category, get_data_on_item
from helpers.change_helpers import request_change, approve_change
from helpers.render_helpers import get_linked_objects
from helpers.form_helpers import validate_form_with_jsonschema
from routes.nation_routes import edit_nation, nation_edit_request, nation_edit_approve
from calculations.field_calculations import calculate_all_fields
from app_core import category_data, mongo, rarity_rankings
from pymongo import ASCENDING


data_item_routes = Blueprint("data_item_routes", __name__)

@data_item_routes.route("/<data_type>")
def data_list(data_type):
    schema, db = get_data_on_category(data_type)
    query_dict = {"_id": 1, "name": 1}
    preview_overall_lookup_dict = {}

    for preview_item in schema.get("preview", {}):
        query_dict[preview_item] = 1
        collection_name = schema.get("properties", {}).get(preview_item, {}).get("collection", None)
        if collection_name:
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
    calculated_fields = calculate_all_fields(item, schema)
    item.update(calculated_fields)
    return render_template(
        "dataItem.html",
        title=item_ref,
        schema=schema,
        item=item,
        linked_objects=linked_objects
    )

@data_item_routes.route("/<data_type>/edit")
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
        if attributes.get("collection") != None:
            related_collection = attributes.get("collection")
            dropdown_options[field] = list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}).sort("name", ASCENDING))
    
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
        if attributes.get("collection") != None:
            related_collection = attributes.get("collection")
            dropdown_options[field] = list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}).sort("name", ASCENDING))
    
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
def data_item_new_approve(data_type):
    schema, db = get_data_on_category(data_type)
        
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collection") != None:
            related_collection = attributes.get("collection")
            dropdown_options[field] = list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}).sort("name", ASCENDING))
    
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
        if attrs.get("collection"):
            related_collection = attrs.get("collection")
            dropdown_options[field] = list(
                mongo.db[related_collection].find(
                    {}, {"name": 1, "_id": 1}
                ).sort("name", ASCENDING)
            )
    
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
        if attributes.get("collection") != None:
            related_collection = attributes.get("collection")
            dropdown_options[field] = list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}).sort("name", ASCENDING))
    
    form.populate_linked_fields(form, dropdown_options)
    
    if not form.validate():
        flash("Form validation failed!")
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
def data_item_edit_approve(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    
    if data_type == "nations": #This should never happen, but just a good fallback
        print("Had to redirect from data_item_routes to nation_routes")
        return nation_edit_request(item_ref)
    
    form = form_generator.get_form(data_type, schema, formdata=request.form)
    
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collection") != None:
            related_collection = attributes.get("collection")
            dropdown_options[field] = list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}).sort("name", ASCENDING))
    
    form.populate_linked_fields(schema, dropdown_options)
    
    if not form.validate():
        flash("Form validation failed!")
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
        collection_name = schema.get("properties", {}).get(preview_item, {}).get("collection", None)
        if collection_name:
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