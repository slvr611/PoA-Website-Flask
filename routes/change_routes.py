from flask import Blueprint, render_template, redirect, flash
from helpers.auth_helpers import admin_required
from helpers.data_helpers import get_data_on_category, get_data_on_item
from helpers.render_helpers import get_linked_objects
from helpers.change_helpers import approve_change, deny_change
from app_core import category_data, mongo, app
from pymongo import DESCENDING, ASCENDING
from bson import ObjectId

# Add max and min functions to Jinja environment
app.jinja_env.globals.update(max=max, min=min)

change_routes = Blueprint("change_routes", __name__)

def get_preview_references(schema, collections_to_preview):
    """Helper function to get preview references for all collections"""
    preview_overall_lookup_dict = {}
    
    for field, collection_name in collections_to_preview.items():
        if collection_name in category_data:
            preview_db = category_data[collection_name]["database"]
            preview_individual_lookup_dict = {}
            preview_data = list(preview_db.find({}, {"_id": 1, "name": 1}))
            for data in preview_data:
                preview_individual_lookup_dict[str(data["_id"])] = {
                    "name": data.get("name", "None"),
                    "link": f"/{collection_name}/item/{data.get('name', data.get('_id', '#'))}"
                }
            preview_overall_lookup_dict[field] = preview_individual_lookup_dict
            # Also add by collection name for direct lookups
            preview_overall_lookup_dict[collection_name] = preview_individual_lookup_dict
    
    return preview_overall_lookup_dict

@change_routes.route("/changes")
def change_list_redirect():
    # Redirect to pending changes by default
    return redirect("/changes/pending")

@change_routes.route("/changes/pending")
@change_routes.route("/changes/pending/page/<int:page>")
def pending_change_list(page=1):
    items_per_page = 10
    schema, db = get_data_on_category("changes")
    query_dict = {"_id": 1, "name": 1}
    collections_to_preview = {}
    target_schemas = {}

    # Get all target collection schemas
    for collection_name in category_data:
        target_schemas[collection_name] = category_data[collection_name]["schema"]

    # Add standard fields to query
    for preview_item in schema.get("preview", {}):
        query_dict[preview_item] = 1
        collection_names = schema.get("properties", {}).get(preview_item, {}).get("collections")
        if collection_names:
            for collection_name in collection_names:
                collections_to_preview[preview_item] = collection_name

    # Count total documents for pagination
    pending_count = db.count_documents({"status": "Pending"})
    
    # Calculate pagination
    total_pages = (pending_count + items_per_page - 1) // items_per_page
    
    # Get paginated results
    skip = (page - 1) * items_per_page
    pending_changes = list(db.find({"status": "Pending"}, query_dict)
                          .sort([("time_requested", ASCENDING), ("time_approved", ASCENDING)])
                          .skip(skip).limit(items_per_page))

    # Add all collection types that appear in changes
    for change in pending_changes:
        if change["target_collection"] in category_data:
            collections_to_preview[change["target_collection"]] = change["target_collection"]
            
            # Add collections for linked fields in the target schema
            target_schema = target_schemas.get(change["target_collection"], {}).get("properties", {})
            for field_name, field_schema in target_schema.items():
                if field_schema.get("bsonType") == "linked_object" and field_schema.get("collections"):
                    for linked_collection in field_schema.get("collections", []):
                        collections_to_preview[linked_collection] = linked_collection

    # Get preview references for all collections
    preview_overall_lookup_dict = get_preview_references(schema, collections_to_preview)

    return render_template(
        "pending_changes.html",
        title="Pending Changes",
        changes=pending_changes,
        schema=schema,
        preview_references=preview_overall_lookup_dict,
        target_schemas=target_schemas,
        current_page=page,
        total_pages=total_pages,
        total_count=pending_count
    )

@change_routes.route("/changes/archived")
@change_routes.route("/changes/archived/page/<int:page>")
def archived_change_list(page=1):
    items_per_page = 10
    schema, db = get_data_on_category("changes")
    query_dict = {"_id": 1, "name": 1}
    collections_to_preview = {}
    target_schemas = {}

    # Get all target collection schemas
    for collection_name in category_data:
        target_schemas[collection_name] = category_data[collection_name]["schema"]

    # Add standard fields to query
    for preview_item in schema.get("preview", {}):
        query_dict[preview_item] = 1
        collection_names = schema.get("properties", {}).get(preview_item, {}).get("collections")
        if collection_names:
            for collection_name in collection_names:
                collections_to_preview[preview_item] = collection_name

    # Count total documents for pagination
    archived_count = db.count_documents({"status": {"$ne": "Pending"}})
    
    # Calculate pagination
    total_pages = (archived_count + items_per_page - 1) // items_per_page
    
    # Get paginated results
    skip = (page - 1) * items_per_page
    archived_changes = list(db.find({"status": {"$ne": "Pending"}}, query_dict)
                           .sort([("time_requested", DESCENDING), ("time_approved", DESCENDING)])
                           .skip(skip).limit(items_per_page))

    # Add all collection types that appear in changes
    for change in archived_changes:
        if change["target_collection"] in category_data:
            collections_to_preview[change["target_collection"]] = change["target_collection"]
            
            # Add collections for linked fields in the target schema
            target_schema = target_schemas.get(change["target_collection"], {}).get("properties", {})
            for field_name, field_schema in target_schema.items():
                if field_schema.get("bsonType") == "linked_object" and field_schema.get("collections"):
                    for linked_collection in field_schema.get("collections", []):
                        collections_to_preview[linked_collection] = linked_collection

    # Get preview references for all collections
    preview_overall_lookup_dict = get_preview_references(schema, collections_to_preview)

    return render_template(
        "archived_changes.html",
        title="Archived Changes",
        changes=archived_changes,
        schema=schema,
        preview_references=preview_overall_lookup_dict,
        target_schemas=target_schemas,
        current_page=page,
        total_pages=total_pages,
        total_count=archived_count
    )

@change_routes.route("/changes/item/<item_ref>")
def change_item(item_ref):
    schema, db, item = get_data_on_item("changes", item_ref)
    target_schema = None
    if item["target_collection"] in category_data:
        target_schema = category_data[item["target_collection"]]["schema"]

    linked_objects = get_linked_objects(schema, item)

    if item["target_collection"] and item["target"]:
        obj = mongo.db[item["target_collection"]].find_one({"_id": item["target"]}, {"name": 1, "_id": 1})
        if obj:
            linked_objects["target"] = {
                "name": obj.get("name", obj["_id"]),
                "link": f"/{item['target_collection']}/item/{obj.get('name', obj['_id'])}"
            }
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

@change_routes.route("/changes/item/<item_ref>/approve")
@admin_required
def approve_change_route(item_ref):
    try:
        change_id = ObjectId(item_ref)
        approve_change(change_id)
    except Exception as e:
        print(f"Error converting to ObjectId: {e}")

    return redirect("/changes")

@change_routes.route("/changes/item/<item_ref>/deny")
@admin_required
def deny_change_route(item_ref):
    try:
        change_id = ObjectId(item_ref)
        deny_change(change_id)
    except Exception as e:
        print(f"Error converting to ObjectId: {e}")

    return redirect("/changes")

@change_routes.route("/changes/edit/<item_ref>")
def change_edit(item_ref):
    flash("No editing changes!")
    return redirect("/changes")

@change_routes.route("/changes/edit/<item_ref>/request")
def change_edit_request(item_ref):
    flash("No editing changes!")
    return redirect("/changes")

@change_routes.route("/changes/edit/<item_ref>/save")
def change_edit_save(item_ref):
    flash("No editing changes!")
    return redirect("/changes")

@change_routes.route("/changes/delete/<item_ref>")
def change_delete(item_ref):
    flash("No deleting changes!")
    return redirect("/changes")

@change_routes.route("/changes/delete/<item_ref>/request")
def change_delete_request(item_ref):
    flash("No deleting changes!")
    return redirect("/changes")

@change_routes.route("/changes/delete/<item_ref>/save")
def change_delete_save(item_ref):
    flash("No deleting changes!")
    return redirect("/changes")

@change_routes.route("/changes/clone/<item_ref>")
def change_clone(item_ref):
    flash("No copying changes!")
    return redirect("/changes")

@change_routes.route("/changes/clone/<item_ref>/request")
def change_clone_request(item_ref):
    flash("No copying changes!")
    return redirect("/changes")

@change_routes.route("/changes/clone/<item_ref>/save")
def change_clone_save(item_ref):
    flash("No copying changes!")
    return redirect("/changes")

@change_routes.route("/<data_type>/changes/pending")
@change_routes.route("/<data_type>/changes/pending/page/<int:page>")
def data_type_pending_changes(data_type, page=1):
    # Verify data_type exists
    if data_type not in category_data:
        flash(f"Unknown data type: {data_type}")
        return redirect("/changes/pending")
    
    items_per_page = 10
    schema, db = get_data_on_category("changes")
    query_dict = {"_id": 1, "name": 1}
    collections_to_preview = {}
    target_schemas = {}

    # Get all target collection schemas
    for collection_name in category_data:
        target_schemas[collection_name] = category_data[collection_name]["schema"]

    # Add standard fields to query
    for preview_item in schema.get("preview", {}):
        query_dict[preview_item] = 1
        collection_names = schema.get("properties", {}).get(preview_item, {}).get("collections")
        if collection_names:
            for collection_name in collection_names:
                collections_to_preview[preview_item] = collection_name

    # Count total documents for pagination - filter by data_type
    pending_count = db.count_documents({"status": "Pending", "target_collection": data_type})
    
    # Calculate pagination
    total_pages = (pending_count + items_per_page - 1) // items_per_page
    
    # Get paginated results - filter by data_type
    skip = (page - 1) * items_per_page
    pending_changes = list(db.find(
        {"status": "Pending", "target_collection": data_type}, 
        query_dict
    ).sort([("time_requested", ASCENDING), ("time_approved", ASCENDING)])
     .skip(skip).limit(items_per_page))

    # Add the data_type to collections_to_preview
    collections_to_preview[data_type] = data_type
    
    # Add collections for linked fields in the target schema
    target_schema = target_schemas.get(data_type, {}).get("properties", {})
    for field_name, field_schema in target_schema.items():
        if field_schema.get("bsonType") == "linked_object" and field_schema.get("collections"):
            for linked_collection in field_schema.get("collections", []):
                collections_to_preview[linked_collection] = linked_collection

    # Get preview references for all collections
    preview_overall_lookup_dict = get_preview_references(schema, collections_to_preview)

    return render_template(
        "data_type_changes.html",
        title=f"Pending Changes for {category_data[data_type]['pluralName']}",
        changes=pending_changes,
        schema=schema,
        preview_references=preview_overall_lookup_dict,
        target_schemas=target_schemas,
        current_page=page,
        total_pages=total_pages,
        total_count=pending_count,
        data_type=data_type,
        change_type="pending",
        collection_name=category_data[data_type]['pluralName']
    )

@change_routes.route("/<data_type>/changes/archived")
@change_routes.route("/<data_type>/changes/archived/page/<int:page>")
def data_type_archived_changes(data_type, page=1):
    # Verify data_type exists
    if data_type not in category_data:
        flash(f"Unknown data type: {data_type}")
        return redirect("/changes/archived")
    
    items_per_page = 10
    schema, db = get_data_on_category("changes")
    query_dict = {"_id": 1, "name": 1}
    collections_to_preview = {}
    target_schemas = {}

    # Get all target collection schemas
    for collection_name in category_data:
        target_schemas[collection_name] = category_data[collection_name]["schema"]

    # Add standard fields to query
    for preview_item in schema.get("preview", {}):
        query_dict[preview_item] = 1
        collection_names = schema.get("properties", {}).get(preview_item, {}).get("collections")
        if collection_names:
            for collection_name in collection_names:
                collections_to_preview[preview_item] = collection_name

    # Count total documents for pagination - filter by data_type
    archived_count = db.count_documents({"status": {"$ne": "Pending"}, "target_collection": data_type})
    
    # Calculate pagination
    total_pages = (archived_count + items_per_page - 1) // items_per_page
    
    # Get paginated results - filter by data_type
    skip = (page - 1) * items_per_page
    archived_changes = list(db.find(
        {"status": {"$ne": "Pending"}, "target_collection": data_type}, 
        query_dict
    ).sort([("time_requested", DESCENDING), ("time_approved", DESCENDING)])
     .skip(skip).limit(items_per_page))

    # Add the data_type to collections_to_preview
    collections_to_preview[data_type] = data_type
    
    # Add collections for linked fields in the target schema
    target_schema = target_schemas.get(data_type, {}).get("properties", {})
    for field_name, field_schema in target_schema.items():
        if field_schema.get("bsonType") == "linked_object" and field_schema.get("collections"):
            for linked_collection in field_schema.get("collections", []):
                collections_to_preview[linked_collection] = linked_collection

    # Get preview references for all collections
    preview_overall_lookup_dict = get_preview_references(schema, collections_to_preview)

    return render_template(
        "data_type_changes.html",
        title=f"Archived Changes for {category_data[data_type]['pluralName']}",
        changes=archived_changes,
        schema=schema,
        preview_references=preview_overall_lookup_dict,
        target_schemas=target_schemas,
        current_page=page,
        total_pages=total_pages,
        total_count=archived_count,
        data_type=data_type,
        change_type="archived",
        collection_name=category_data[data_type]['pluralName']
    )

@change_routes.route("/<data_type>/item/<item_ref>/changes/pending")
@change_routes.route("/<data_type>/item/<item_ref>/changes/pending/page/<int:page>")
def item_pending_changes(data_type, item_ref, page=1):
    # Verify data_type exists
    if data_type not in category_data:
        flash(f"Unknown data type: {data_type}")
        return redirect("/changes/pending")
    
    # Get the item to find its ID
    _, _, item = get_data_on_item(data_type, item_ref)
    item_id = item["_id"]
    
    items_per_page = 10
    schema, db = get_data_on_category("changes")
    query_dict = {"_id": 1, "name": 1}
    collections_to_preview = {}
    target_schemas = {}

    # Get all target collection schemas
    for collection_name in category_data:
        target_schemas[collection_name] = category_data[collection_name]["schema"]

    # Add standard fields to query
    for preview_item in schema.get("preview", {}):
        query_dict[preview_item] = 1
        collection_names = schema.get("properties", {}).get(preview_item, {}).get("collections")
        if collection_names:
            for collection_name in collection_names:
                collections_to_preview[preview_item] = collection_name

    # Count total documents for pagination - filter by data_type and item_id
    pending_count = db.count_documents({
        "status": "Pending", 
        "target_collection": data_type,
        "target": item_id
    })
    
    # Calculate pagination
    total_pages = (pending_count + items_per_page - 1) // items_per_page
    
    # Get paginated results - filter by data_type and item_id
    skip = (page - 1) * items_per_page
    pending_changes = list(db.find(
        {
            "status": "Pending", 
            "target_collection": data_type,
            "target": item_id
        }, 
        query_dict
    ).sort([("time_requested", ASCENDING), ("time_approved", ASCENDING)])
     .skip(skip).limit(items_per_page))

    # Add the data_type to collections_to_preview
    collections_to_preview[data_type] = data_type
    
    # Add collections for linked fields in the target schema
    target_schema = target_schemas.get(data_type, {}).get("properties", {})
    for field_name, field_schema in target_schema.items():
        if field_schema.get("bsonType") == "linked_object" and field_schema.get("collections"):
            for linked_collection in field_schema.get("collections", []):
                collections_to_preview[linked_collection] = linked_collection

    # Get preview references for all collections
    preview_overall_lookup_dict = get_preview_references(schema, collections_to_preview)

    return render_template(
        "item_changes.html",
        title=f"Pending Changes for {item_ref}",
        changes=pending_changes,
        schema=schema,
        preview_references=preview_overall_lookup_dict,
        target_schemas=target_schemas,
        current_page=page,
        total_pages=total_pages,
        total_count=pending_count,
        data_type=data_type,
        item_ref=item_ref,
        change_type="pending",
        collection_name=category_data[data_type]['singularName']
    )

@change_routes.route("/<data_type>/item/<item_ref>/changes/archived")
@change_routes.route("/<data_type>/item/<item_ref>/changes/archived/page/<int:page>")
def item_archived_changes(data_type, item_ref, page=1):
    # Verify data_type exists
    if data_type not in category_data:
        flash(f"Unknown data type: {data_type}")
        return redirect("/changes/archived")
    
    # Get the item to find its ID
    _, _, item = get_data_on_item(data_type, item_ref)
    item_id = item["_id"]
    
    items_per_page = 10
    schema, db = get_data_on_category("changes")
    query_dict = {"_id": 1, "name": 1}
    collections_to_preview = {}
    target_schemas = {}

    # Get all target collection schemas
    for collection_name in category_data:
        target_schemas[collection_name] = category_data[collection_name]["schema"]

    # Add standard fields to query
    for preview_item in schema.get("preview", {}):
        query_dict[preview_item] = 1
        collection_names = schema.get("properties", {}).get(preview_item, {}).get("collections")
        if collection_names:
            for collection_name in collection_names:
                collections_to_preview[preview_item] = collection_name

    # Count total documents for pagination - filter by data_type and item_id
    archived_count = db.count_documents({
        "status": {"$ne": "Pending"}, 
        "target_collection": data_type,
        "target": item_id
    })
    
    # Calculate pagination
    total_pages = (archived_count + items_per_page - 1) // items_per_page
    
    # Get paginated results - filter by data_type and item_id
    skip = (page - 1) * items_per_page
    archived_changes = list(db.find(
        {
            "status": {"$ne": "Pending"}, 
            "target_collection": data_type,
            "target": item_id
        }, 
        query_dict
    ).sort([("time_requested", DESCENDING), ("time_approved", DESCENDING)])
     .skip(skip).limit(items_per_page))

    # Add the data_type to collections_to_preview
    collections_to_preview[data_type] = data_type
    
    # Add collections for linked fields in the target schema
    target_schema = target_schemas.get(data_type, {}).get("properties", {})
    for field_name, field_schema in target_schema.items():
        if field_schema.get("bsonType") == "linked_object" and field_schema.get("collections"):
            for linked_collection in field_schema.get("collections", []):
                collections_to_preview[linked_collection] = linked_collection

    # Get preview references for all collections
    preview_overall_lookup_dict = get_preview_references(schema, collections_to_preview)

    return render_template(
        "item_changes.html",
        title=f"Archived Changes for {item_ref}",
        changes=archived_changes,
        schema=schema,
        preview_references=preview_overall_lookup_dict,
        target_schemas=target_schemas,
        current_page=page,
        total_pages=total_pages,
        total_count=archived_count,
        data_type=data_type,
        item_ref=item_ref,
        change_type="archived",
        collection_name=category_data[data_type]['singularName']
    )
