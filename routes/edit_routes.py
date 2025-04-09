from flask import Blueprint, render_template, request, redirect, flash
from forms import form_generator, EnhancedNationEditForm, validate_form_with_jsonschema
from helpers.data_helpers import get_data_on_category, get_data_on_item
from helpers.render_helpers import get_linked_objects
from app_core import category_data, mongo, json_data
from pymongo import ASCENDING

edit_routes = Blueprint("edit_routes", __name__)

@edit_routes.route("/<data_type>/edit/<item_ref>", methods=["GET"])
def data_item_edit(data_type, item_ref):
    if data_type == "nations":
        return edit_nation(item_ref)

    schema, db, item = get_data_on_item(data_type, item_ref)
    dropdown_options = {}
    for field, attr in schema["properties"].items():
        if attr.get("collection"):
            dropdown_options[field] = list(mongo.db[attr["collection"]].find({}, {"_id": 1, "name": 1}).sort("name", ASCENDING))

    linked_objects = get_linked_objects(schema, item)
    form = form_generator.get_form(data_type, schema, item=item)
    form_generator.populate_linked_fields(form, dropdown_options)

    return render_template(
        "dataItemEdit.html",
        title="Edit " + item_ref,
        schema=schema,
        form=form,
        item=item,
        dropdown_options=dropdown_options,
        linked_objects=linked_objects
    )