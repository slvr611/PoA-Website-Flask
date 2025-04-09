from flask import Blueprint, render_template, redirect
from helpers.data_helpers import get_data_on_category, get_data_on_item
from helpers.render_helpers import get_linked_objects
from calculations.field_calculations import calculate_all_fields
from app_core import category_data, mongo, json_data
from pymongo import ASCENDING, DESCENDING
from bson import ObjectId
from forms import form_generator, validate_form_with_jsonschema, EnhancedNationEditForm

nation_routes = Blueprint("nation_routes", __name__)

@nation_routes.route("/nations/item/<item_ref>")
def nation_item(item_ref):
    schema, db, nation = get_data_on_item("nations", item_ref)
    linked_objects = get_linked_objects(schema, nation)
    calculated_fields = calculate_all_fields(nation, schema)
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
        districts_config=json_data["districts"]
    )

@nation_routes.route("/nations/edit/<item_ref>", methods=["GET"])
def edit_nation(item_ref):
    schema, db, nation = get_data_on_item("nations", item_ref)
    
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collection") != None:
            related_collection = attributes.get("collection")
            dropdown_options[field] = list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}).sort("name", ASCENDING))
    
    linked_objects = get_linked_objects(schema, nation)
    
    calculated_fields = calculate_all_fields(nation, schema)
    nation.update(calculated_fields)
    
    # Create form using EnhancedNationEditForm
    
    form = EnhancedNationEditForm.create_from_nation(nation, schema, json_data, dropdown_options)
    
    print(form._fields)
    
    template = render_template(
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
    return template

@nation_routes.route("/nations/edit/<item_ref>/request", methods=["POST"])
def nation_edit_request(item_ref):
    schema, db, nation = get_data_on_item("nations", item_ref)
    
    # Get dropdown options for linked fields
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collection") != None:
            related_collection = attributes.get("collection")
            dropdown_options[field] = list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}).sort("name", ASCENDING))
    
    # Create form and populate with request data
    form = EnhancedNationEditForm(request.form, obj=nation)
    
    form.populate_obj(nation)
    
    if not form.validate():
        flash("Form validation failed!")
        return redirect("/nations/edit/" + item_ref)
    
    # Convert form to dictionary
    form_data = form.to_dict()
    
    # Additional JSON schema validation
    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect("/nations/edit/" + item_ref)
    
    if form_data["name"] != item_ref and db.find_one({"name": form_data["name"]}):
        flash("Name must be unique!")
        return redirect(f"/nations/edit/{item_ref}")
    
    item_id = nation["_id"]
    reason = form.reason.data or "No Reason Given"
    before_data = nation
    after_data = form_data
    
    change_id = request_change(
        data_type="nations",
        item_id=item_id,
        change_type="Update",
        before_data=before_data,
        after_data=after_data,
        reason=reason
    )
    
    flash(f"Change request #{change_id} created and awaits admin approval.")
    
    return redirect("/nations")

@nation_routes.route("/nations/edit/<item_ref>/save", methods=["POST"])
def nation_edit_approve(item_ref):
    schema, db, nation = get_data_on_item("nations", item_ref)
    
    # Get dropdown options for linked fields
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("collection") != None:
            related_collection = attributes.get("collection")
            dropdown_options[field] = list(mongo.db[related_collection].find({}, {"name": 1, "_id": 1}).sort("name", ASCENDING))
    
    # Create form and populate with request data
    form = EnhancedNationEditForm(request.form, obj=nation)
    
    print(form.stability.choices)
    
    print(form.to_dict())
    
    #TODO: Automate this
    # Repopulate select fields choices
    form.region.choices = [("", schema["properties"].get("region", {}).get("noneResult", "None"))] + \
                          [(str(option["_id"]), option["name"]) for option in dropdown_options.get("region", [])]
    form.primary_race.choices = [("", schema["properties"].get("primary_race", {}).get("noneResult", "None"))] + \
                                [(str(option["_id"]), option["name"]) for option in dropdown_options.get("primary_race", [])]
    form.primary_culture.choices = [("", schema["properties"].get("primary_culture", {}).get("noneResult", "None"))] + \
                                   [(str(option["_id"]), option["name"]) for option in dropdown_options.get("primary_culture", [])]
    form.primary_religion.choices = [("", schema["properties"].get("primary_religion", {}).get("noneResult", "None"))] + \
                                    [(str(option["_id"]), option["name"]) for option in dropdown_options.get("primary_religion", [])]
    form.overlord.choices = [("", schema["properties"].get("overlord", {}).get("noneResult", "None"))] + \
                            [(str(option["_id"]), option["name"]) for option in dropdown_options.get("overlord", [])]
    form.stability.choices = [(option, option) for option in schema["properties"].get("stability", {}).get("enum", [])] or \
                                 [("Balanced", "Balanced"), ("Unstable", "Unstable"), ("Stable", "Stable")]
    form.vassal_type.choices = [(option, option) for option in schema["properties"].get("vassal_type", {}).get("enum", [])] or \
                                [("None", "None"), ("Tributary", "Tributary"), ("Mercantile", "Mercantile")]
    form.compliance.choices = [(option, option) for option in schema["properties"].get("compliance", {}).get("enum", [])] or \
                              [("None", "None"), ("Neutral", "Neutral"), ("Loyal", "Loyal"), ("Disloyal", "Disloyal")]
    form.origin.choices = [(option, option) for option in schema["properties"].get("origin", {}).get("enum", [])] or \
                          [("Unknown", "Unknown"), ("Settled", "Settled"), ("Conquered", "Conquered")]
    # Similarly, if you have other dynamic SelectFields (like for laws), you need to set their choices as well.
    
    # For nested FieldList of SelectField for districts, set each entry’s choices.
    district_choices = [("", "Empty Slot")]
    for key, district in json_data["districts"].items():
        district_choices.append((key, district["display_name"]))
    for entry in form.districts:
        entry.choices = district_choices


    
    print(form.to_dict())
    
    if not form.validate():
        flash(f"Validation Error: {form.errors}")
        return redirect("/nations/edit/" + item_ref)
    
    # Convert form to dictionary
    form_data = form.to_dict()
    
    # Additional JSON schema validation
    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect("/nations/edit/" + item_ref)
    
    if form_data["name"] != item_ref and db.find_one({"name": form_data["name"]}):
        flash("Name must be unique!")
        return redirect(f"/nations/edit/{item_ref}")
    
    item_id = nation["_id"]
    reason = form.reason.data or "No Reason Given"
    before_data = nation
    after_data = form_data
    
    change_id = request_change(
        data_type="nations",
        item_id=item_id,
        change_type="Update",
        before_data=before_data,
        after_data=after_data,
        reason=reason
    )
    
    approve_change(change_id)
    
    flash(f"Change request #{change_id} created and approved.")
    
    return redirect("/nations")
