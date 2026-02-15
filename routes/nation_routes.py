from flask import Blueprint, render_template, request, redirect, flash, g, current_app
from copy import deepcopy
from helpers.data_helpers import get_data_on_category, get_data_on_item, get_dropdown_options
from helpers.render_helpers import get_linked_objects
from helpers.change_helpers import request_change, approve_change, system_approve_change
from helpers.form_helpers import validate_form_with_jsonschema
from helpers.auth_helpers import owner_required
from app_core import category_data, mongo, json_data, find_dict_in_list
from helpers.auth_helpers import admin_required
from pymongo import ASCENDING
from forms import form_generator, wtform_to_json
import json
from time import perf_counter
from calculations.field_calculations import calculate_all_fields
from bson import ObjectId

nation_routes = Blueprint("nation_routes", __name__)

POP_PAGE_SIZE = 100

def _to_object_ids(id_list):
    object_ids = []
    for item_id in id_list:
        try:
            object_ids.append(ObjectId(item_id))
        except Exception:
            continue
    return object_ids

def _fetch_nation_pops_page(nation_id, page, page_size, pops_schema):
    nation_id_str = str(nation_id)
    pop_query = {"nation": nation_id_str}
    total_pops = mongo.db.pops.count_documents(pop_query)

    total_pages = max(1, (total_pops + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    skip = (page - 1) * page_size

    sort_by = pops_schema.get("sort_by", ["race", "culture", "religion"])
    sort_tuples = [(field, ASCENDING) for field in sort_by] if isinstance(sort_by, list) else [(sort_by, ASCENDING)]

    pops = list(
        mongo.db.pops.find(pop_query, {"_id": 1, "race": 1, "culture": 1, "religion": 1})
        .sort(sort_tuples)
        .skip(skip)
        .limit(page_size)
    )

    race_ids = list({pop.get("race", "") for pop in pops if pop.get("race")})
    culture_ids = list({pop.get("culture", "") for pop in pops if pop.get("culture")})
    religion_ids = list({pop.get("religion", "") for pop in pops if pop.get("religion")})

    races = list(mongo.db.races.find({"_id": {"$in": _to_object_ids(race_ids)}}, {"_id": 1, "name": 1}))
    cultures = list(mongo.db.cultures.find({"_id": {"$in": _to_object_ids(culture_ids)}}, {"_id": 1, "name": 1}))
    religions = list(mongo.db.religions.find({"_id": {"$in": _to_object_ids(religion_ids)}}, {"_id": 1, "name": 1}))

    race_lookup = {str(item["_id"]): {"name": item.get("name", "Unknown"), "link": f"/races/item/{item.get('name', item['_id'])}"} for item in races}
    culture_lookup = {str(item["_id"]): {"name": item.get("name", "Unknown"), "link": f"/cultures/item/{item.get('name', item['_id'])}"} for item in cultures}
    religion_lookup = {str(item["_id"]): {"name": item.get("name", "Unknown"), "link": f"/religions/item/{item.get('name', item['_id'])}"} for item in religions}

    for pop in pops:
        pop["link"] = f"/pops/item/{pop.get('_id')}"
        pop["linked_objects"] = {
            "race": race_lookup.get(pop.get("race", ""), None),
            "culture": culture_lookup.get(pop.get("culture", ""), None),
            "religion": religion_lookup.get(pop.get("religion", ""), None),
        }

    return pops, {
        "page": page,
        "page_size": page_size,
        "total_items": total_pops,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_page": page - 1,
        "next_page": page + 1,
    }



@nation_routes.route("/nations/item/<item_ref>")
def nation_item(item_ref):
    """Display a nation's details"""
    request_start = perf_counter()
    timings = {}

    phase_start = perf_counter()
    schema, db, nation = get_data_on_item("nations", item_ref)
    timings["get_data_on_item_ms"] = round((perf_counter() - phase_start) * 1000, 2)

    phase_start = perf_counter()
    linked_objects = get_linked_objects(schema, nation, exclude_fields={"pops"})
    timings["get_linked_objects_ms"] = round((perf_counter() - phase_start) * 1000, 2)

    phase_start = perf_counter()
    pop_page = request.args.get("pop_page", default=1, type=int)
    pop_page = pop_page if pop_page and pop_page > 0 else 1
    pops, pop_pagination = _fetch_nation_pops_page(
        nation.get("_id"),
        pop_page,
        POP_PAGE_SIZE,
        schema.get("properties", {}).get("pops", {})
    )
    linked_objects["pops"] = pops
    timings["load_pops_page_ms"] = round((perf_counter() - phase_start) * 1000, 2)

    phase_start = perf_counter()
    user_is_owner = False
    if g.user:
        user = mongo.db.players.find_one({"id": g.user.get("id")})
        if user:
            user_characters = list(mongo.db.characters.find({"player": str(user["_id"])}))
            for character in user_characters:
                if str(character.get("ruling_nation_org", "")) == str(nation["_id"]):
                    user_is_owner = True
                    break
    timings["ownership_check_ms"] = round((perf_counter() - phase_start) * 1000, 2)
    
    if "jobs" not in nation:
        nation["jobs"] = {}

    calc_timings = {}
    breakdowns = nation.get("breakdowns", None)
    required_breakdown_keys = {"stability_gain_chance", "stability_loss_chance", "resource_production", "resource_consumption", "money_income"}
    has_cached_breakdowns = isinstance(breakdowns, dict) and required_breakdown_keys.issubset(set(breakdowns.keys()))

    timings["calculate_all_fields_ms"] = 0.0
    timings["used_cached_calculations"] = True

    # Fallback path for older nations missing cached calculations or breakdowns.
    if not has_cached_breakdowns:
        timings["used_cached_calculations"] = False
        phase_start = perf_counter()
        calculated_values, breakdowns = calculate_all_fields(
            deepcopy(nation),
            schema,
            "nation",
            return_breakdowns=True,
            instrumentation=calc_timings
        )
        timings["calculate_all_fields_ms"] = round((perf_counter() - phase_start) * 1000, 2)
        nation.update(calculated_values)
        nation["breakdowns"] = breakdowns

        phase_start = perf_counter()
        mongo.db.nations.update_one(
            {"_id": nation["_id"]},
            {
                "$set": {**calculated_values, "breakdowns": breakdowns},
                "$unset": {"_calc_cache": ""}
            }
        )
        timings["cache_backfill_ms"] = round((perf_counter() - phase_start) * 1000, 2)
    else:
        timings["cache_backfill_ms"] = 0.0
    
    phase_start = perf_counter()
    rendered = render_template(
        "nation_owner.html",
        title=item_ref,
        schema=schema,
        nation=nation,
        linked_objects=linked_objects,
        json_data=json_data,
        cities_config=json_data["cities"],
        user_is_owner=user_is_owner,
        find_dict_in_list=find_dict_in_list,
        breakdowns=breakdowns,
        pop_pagination=pop_pagination
    )
    timings["render_template_ms"] = round((perf_counter() - phase_start) * 1000, 2)
    timings["total_request_ms"] = round((perf_counter() - request_start) * 1000, 2)

    current_app.logger.info(
        "Nation page timing: nation=%s timings=%s calc_timings=%s",
        item_ref,
        timings,
        calc_timings
    )

    return rendered

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
        json_data=json_data,
        find_dict_in_list=find_dict_in_list
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
    if "name" in form_data:
        form_data["name"] = form_data.get("name", "").strip()
    
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
    if "name" in form_data:
        form_data["name"] = form_data.get("name", "").strip()

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
