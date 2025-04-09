from flask import Blueprint, render_template, redirect
from helpers.data_helpers import get_data_on_category, get_data_on_item
from helpers.render_helpers import get_linked_objects
from calculations.field_calculations import calculate_all_fields
from app_core import category_data, mongo
from pymongo import DESCENDING
from bson import ObjectId

change_routes = Blueprint("change_routes", __name__)

@change_routes.route("/changes")
def change_list():
    schema, db = get_data_on_category("changes")
    query_dict = {"_id": 1, "name": 1}
    preview_overall_lookup_dict = {}

    for preview_item in schema.get("preview", {}):
        query_dict[preview_item] = 1
        collection_name = schema.get("properties", {}).get(preview_item, {}).get("collection")
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

    items = list(db.find({}, query_dict).sort([("time_requested", DESCENDING), ("time_approved", DESCENDING)]))
    return render_template(
        "dataList.html",
        title=category_data["changes"]["pluralName"],
        items=items,
        schema=schema,
        preview_references=preview_overall_lookup_dict
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
def approve_change_route(item_ref):
    from helpers.change_helpers import approve_change

    try:
        change_id = ObjectId(item_ref)
        approve_change(change_id)
    except Exception as e:
        print(f"Error converting to ObjectId: {e}")

    return redirect("/changes")
