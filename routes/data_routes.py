from flask import Blueprint, render_template, request, redirect, flash
from helpers.data_helpers import get_data_on_category, get_data_on_item
from helpers.render_helpers import get_linked_objects
from calculations.field_calculations import calculate_all_fields
from app_core import category_data, mongo
from pymongo import ASCENDING


data_routes = Blueprint("data_routes", __name__)

@data_routes.route("/<data_type>")
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

    items = list(db.find({}, query_dict).sort("name", ASCENDING))
    return render_template(
        "dataList.html",
        title=category_data[data_type]["pluralName"],
        items=items,
        schema=schema,
        preview_references=preview_overall_lookup_dict
    )

@data_routes.route("/<data_type>/item/<item_ref>")
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