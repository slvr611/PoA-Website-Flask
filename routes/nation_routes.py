from flask import Blueprint, render_template, request, redirect, flash, g, current_app, jsonify, abort
import os
import re
from copy import deepcopy
from helpers.data_helpers import get_data_on_category, get_data_on_item, get_dropdown_options
from helpers.render_helpers import get_linked_objects
from helpers.change_helpers import request_change, approve_change, system_approve_change, deep_merge
from helpers.form_helpers import validate_form_with_jsonschema
from helpers.auth_helpers import owner_required
from app_core import category_data, mongo, json_data, find_dict_in_list, upload_bytes_to_s3
from helpers.auth_helpers import admin_required
from pymongo import ASCENDING
from forms import form_generator, wtform_to_json
import json
from time import perf_counter
from calculations.field_calculations import calculate_all_fields, _resolve_def, check_upgrade_requirements
from bson import ObjectId
from helpers.visibility_helpers import get_item_visibility, log_visibility_bypass, strip_form_data_to_tier

nation_routes = Blueprint("nation_routes", __name__)

POP_PAGE_SIZE = 100

# Process-level cache for static game config data (cleared by process restart)
import time as _time
_DISTRICT_CACHE: dict = {}
_DISTRICT_CACHE_TTL = 300  # 5 minutes

def _get_district_defs():
    """Return (district_defs_full, district_defs_map, district_categories), cached."""
    now = _time.time()
    if _DISTRICT_CACHE and _DISTRICT_CACHE.get("ts", 0) + _DISTRICT_CACHE_TTL > now:
        return (
            _DISTRICT_CACHE["defs"],
            _DISTRICT_CACHE["defs_map"],
            _DISTRICT_CACHE["categories"],
        )
    from pymongo import ASCENDING as _ASC
    defs_full = list(mongo.db.district_defs.find(
        {}, {"_id": 0}
    ).sort([("category", _ASC), ("tier", _ASC), ("display_name", _ASC)]))
    defs_map = {d["key"]: d for d in defs_full if "key" in d}
    categories = {
        c["key"]: c.get("display_name", c["key"])
        for c in mongo.db.district_categories.find({}, {"key": 1, "display_name": 1, "_id": 0})
    }
    _DISTRICT_CACHE.update({"ts": now, "defs": defs_full, "defs_map": defs_map, "categories": categories})
    return defs_full, defs_map, categories


def _validate_tech_costs(form_data):
    """Return (True, None) or (False, error_message) checking each tech's submitted
    cost is at least floor(base_cost / 2).  Techs with base_cost == 0 are skipped."""
    tech_json = json_data.get("tech", {})
    technologies = form_data.get("technologies", {})
    if not isinstance(technologies, dict):
        return True, None
    errors = []
    for tech_id, tech_data in technologies.items():
        if not isinstance(tech_data, dict):
            continue
        submitted_cost = tech_data.get("cost")
        if submitted_cost is None:
            continue
        base_cost = tech_json.get(tech_id, {}).get("cost", 0)
        if base_cost <= 0:
            continue
        min_cost = base_cost // 2
        if submitted_cost < min_cost:
            display_name = tech_json.get(tech_id, {}).get("display_name", tech_id)
            errors.append(
                f"'{display_name}' has a minimum cost of {min_cost} "
                f"(half of its base cost of {base_cost}), but {submitted_cost} was submitted."
            )
    if errors:
        prefix = "Techs have a minimum cost equal to half their base cost. "
        return False, prefix + " | ".join(errors)
    return True, None


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
        mongo.db.pops.find(pop_query, {"_id": 1, "race": 1, "culture": 1, "religion": 1, "slave": 1})
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

    # Before the normal lookup, check if this is an old name that was renamed.
    # get_data_on_item aborts with 404 if not found, so we check previous_names first.
    if not mongo.db.nations.find_one({"name": item_ref}, {"_id": 1}):
        successor = mongo.db.nations.find_one(
            {"previous_names": item_ref}, {"name": 1}
        )
        if successor:
            return redirect(f"/nations/item/{successor['name']}", code=301)

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
            user_id_str = str(user["_id"])
            if user_id_str in nation.get("players", []):
                user_is_owner = True
            else:
                user_characters = list(mongo.db.characters.find({"player": user_id_str}))
                for character in user_characters:
                    if str(character.get("ruling_nation_org", "")) == str(nation["_id"]):
                        user_is_owner = True
                        break
    timings["ownership_check_ms"] = round((perf_counter() - phase_start) * 1000, 2)
    
    if not isinstance(nation.get("jobs"), dict):
        nation["jobs"] = {}

    calc_timings = {}
    breakdowns = nation.get("breakdowns", None)
    required_breakdown_keys = {"stability_gain_chance", "stability_loss_chance", "resource_production", "resource_consumption", "money_income", "job_production", "job_consumption"}
    has_cached_breakdowns = isinstance(breakdowns, dict) and required_breakdown_keys.issubset(set(breakdowns.keys())) and "slave_capacity" in nation

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
        from calculations.visibility import collect_visibility_modifiers as _collect_vis_mods
        nation["visibility_modifiers"] = _collect_vis_mods(nation)
        mongo.db.nations.update_one(
            {"_id": nation["_id"]},
            {
                "$set": {**calculated_values, "breakdowns": breakdowns, "visibility_modifiers": nation["visibility_modifiers"]},
                "$unset": {"_calc_cache": ""}
            }
        )
        timings["cache_backfill_ms"] = round((perf_counter() - phase_start) * 1000, 2)
    else:
        timings["cache_backfill_ms"] = 0.0
    
    user_can_edit_pops = user_is_owner or bool(g.user and g.user.get("is_admin"))

    # --- Visibility ---
    from calculations.visibility import get_viewer_nation, compute_visibility, collect_visibility_modifiers
    if "visibility_modifiers" not in nation:
        nation["visibility_modifiers"] = collect_visibility_modifiers(nation)
        mongo.db.nations.update_one(
            {"_id": nation["_id"]},
            {"$set": {"visibility_modifiers": nation["visibility_modifiers"]}}
        )

    visibility_bypassed = bool(
        (g.user and g.user.get("is_admin") and request.args.get("bypass_visibility") == "1")
        or getattr(g, 'is_non_player_admin', False)
    )
    if visibility_bypassed:
        visibility_level = 4
    elif user_is_owner:
        visibility_level = 4
    else:
        viewer_nation = get_viewer_nation(g.user) if g.user else None
        if viewer_nation is None:
            visibility_level = 0
        else:
            visibility_level = compute_visibility(viewer_nation, str(nation["_id"]))

    pending_nation = None
    pending_breakdowns = None
    try:
        pending_changes = list(mongo.db.changes.find({
            "target": nation["_id"],
            "status": "Pending",
            "change_type": "Update",
            "target_collection": "nations"
        }).sort("time_requested", 1))
        if pending_changes:
            merged = deepcopy(nation)
            for change in pending_changes:
                merged = deep_merge(merged, change["after_requested_data"])
            pending_values, pending_breakdowns = calculate_all_fields(
                merged, schema, "nation", return_breakdowns=True
            )
            pending_nation = {**merged, **pending_values}
            if not isinstance(pending_nation.get("jobs"), dict):
                pending_nation["jobs"] = nation.get("jobs", {})
    except Exception as e:
        current_app.logger.warning("Failed to compute pending nation state: %s", e)

    # Build def lookup for DB-driven districts
    district_defs_map = {}
    for _d in nation.get("districts", []):
        if isinstance(_d, dict):
            _dk = _d.get("def_key")
            if _dk and _dk not in district_defs_map:
                _dd = _resolve_def(_d)
                if _dd:
                    district_defs_map[_dk] = _dd

    # Trade routes
    nation_name = nation.get("name", item_ref)
    trade_routes = list(mongo.db.trade_routes.find({
        "$or": [{"nation_a": nation_name}, {"nation_b": nation_name}],
        "status": {"$in": ["pending", "active", "ending"]}
    }))
    _gm = mongo.db.global_modifiers.find_one({"name": "global_modifiers"}, {"session_counter": 1})
    current_session = _gm.get("session_counter", 0) if _gm else 0

    all_players = []
    nation_players = []
    if g.user and g.user.get("is_admin"):
        all_players = list(mongo.db.players.find({}, {"_id": 1, "name": 1}).sort("name", 1))
        player_ids = nation.get("players", [])
        if player_ids:
            try:
                obj_ids = [ObjectId(pid) for pid in player_ids]
                nation_players = list(mongo.db.players.find({"_id": {"$in": obj_ids}}, {"_id": 1, "name": 1}))
            except Exception:
                nation_players = []

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
        user_can_edit_pops=user_can_edit_pops,
        find_dict_in_list=find_dict_in_list,
        breakdowns=breakdowns,
        pop_pagination=pop_pagination,
        pending_nation=pending_nation,
        pending_breakdowns=pending_breakdowns,
        district_defs_map=district_defs_map,
        visibility_level=visibility_level,
        visibility_bypassed=visibility_bypassed,
        trade_routes=trade_routes,
        current_session=current_session,
        editable=False,
        connectable_nations=[],
        all_players=all_players,
        nation_players=nation_players,
    )
    timings["render_template_ms"] = round((perf_counter() - phase_start) * 1000, 2)
    timings["total_request_ms"] = round((perf_counter() - request_start) * 1000, 2)

    # current_app.logger.info(
    #     "Nation page timing: nation=%s timings=%s calc_timings=%s",
    #     item_ref,
    #     timings,
    #     calc_timings
    # )

    return rendered


@nation_routes.route("/nations/item/<item_ref>/set_player", methods=["POST"])
@admin_required
def nation_set_player(item_ref):
    schema, db, nation = get_data_on_item("nations", item_ref)
    action = request.form.get("action")
    player_id = request.form.get("player_id", "").strip()

    current_players = list(nation.get("players", []))

    if action == "add":
        if not player_id:
            flash("No player selected.", "warning")
            return redirect(f"/nations/item/{item_ref}")
        if player_id not in current_players:
            try:
                player = mongo.db.players.find_one({"_id": ObjectId(player_id)}, {"name": 1})
            except Exception:
                player = None
            if not player:
                flash("Player not found.", "error")
                return redirect(f"/nations/item/{item_ref}")
            current_players.append(player_id)
            db.update_one({"_id": nation["_id"]}, {"$set": {"players": current_players}})
            flash(f"Assigned {player.get('name', player_id)} to {nation['name']}.", "success")

    elif action == "remove":
        if player_id in current_players:
            current_players.remove(player_id)
            db.update_one({"_id": nation["_id"]}, {"$set": {"players": current_players}})
            flash("Player removed.", "success")

    return redirect(f"/nations/item/{item_ref}")


def _render_nation_edit(item_ref, form=None):
    """Build and return the nation edit page.

    Pass form=None to load from DB (GET path).  Pass a pre-built form populated
    from request.form to preserve submitted values after a validation failure.
    """
    _r0 = perf_counter()

    schema, db, nation = get_data_on_item("nations", item_ref)
    _r1 = perf_counter()

    # Only query for linked_object fields — NationForm.populate_linked_fields
    # only calls populate_select_field for those; array fields (vassals, pops,
    # diplo_relations, etc.) never consume dropdown_options.
    dropdown_options = {}
    _coll_cache: dict = {}
    for field, attributes in schema["properties"].items():
        if attributes.get("bsonType") == "linked_object" and attributes.get("collections"):
            dropdown_options[field] = []
            for related_collection in attributes["collections"]:
                if related_collection not in _coll_cache:
                    _coll_cache[related_collection] = list(
                        mongo.db[related_collection].find(
                            {}, {"name": 1, "_id": 1}
                        ).sort("name", ASCENDING)
                    )
                dropdown_options[field] += _coll_cache[related_collection]
    _r2 = perf_counter()

    linked_objects = get_linked_objects(schema, nation)
    _r3 = perf_counter()

    if "concessions" in nation and nation["concessions"] is not None:
        if not isinstance(nation["concessions"], dict):
            nation["concessions"] = {}
    else:
        nation["concessions"] = {}

    if form is None:
        form = form_generator.get_form("nations", schema, item=nation)
        form.concessions.data = json.dumps(nation.get("concessions", {}))
    _r4 = perf_counter()

    form.populate_linked_fields(schema, dropdown_options)
    _r5 = perf_counter()

    def _opts(collection):
        # Reuse _coll_cache when available (already fetched for dropdown_options)
        docs = _coll_cache.get(collection) or list(
            mongo.db[collection].find({}, {"_id": 1, "name": 1}).sort("name", ASCENDING)
        )
        return [{"id": str(d["_id"]), "name": d.get("name", "")} for d in docs]

    bulk_edit_options = {
        "nations": _opts("nations"),
        "races": _opts("races"),
        "cultures": _opts("cultures"),
        "religions": _opts("religions"),
    }
    _r6 = perf_counter()

    # District defs — served from a 5-minute process-level cache (static config data)
    district_defs, district_defs_map, district_categories = _get_district_defs()
    _r7 = perf_counter()

    # Trade routes
    nation_name = nation.get("name", item_ref)
    trade_routes = list(mongo.db.trade_routes.find({
        "$or": [{"nation_a": nation_name}, {"nation_b": nation_name}],
        "status": {"$in": ["pending", "active", "ending"]}
    }))
    _gm = mongo.db.global_modifiers.find_one({"name": "global_modifiers"}, {"session_counter": 1})
    current_session = _gm.get("session_counter", 0) if _gm else 0
    from helpers.trade_route_helpers import get_connectable_nations
    connectable_nations = get_connectable_nations(nation_name, nation.get("trade_speed", 1))
    _r8 = perf_counter()

    visibility_level, visibility_bypassed = get_item_visibility(
        "nations", nation,
        user=g.user,
        view_access_level=g.view_access_level,
        is_non_player_admin=g.is_non_player_admin,
    )
    if visibility_bypassed and g.user and g.user.get("is_admin") and request.args.get("bypass_visibility") == "1":
        log_visibility_bypass(
            page_url=request.url,
            nation_name=nation.get("name", ""),
            source="nation_edit",
            user=g.user,
        )
    _r9 = perf_counter()

    rendered = render_template(
        "nation_owner_edit.html",
        form=form,
        form_json=wtform_to_json(form),
        title="Edit " + item_ref,
        schema=schema,
        nation=nation,
        dropdown_options=dropdown_options,
        linked_objects=linked_objects,
        json_data=json_data,
        find_dict_in_list=find_dict_in_list,
        bulk_edit_options=bulk_edit_options,
        district_defs=district_defs,
        district_defs_map=district_defs_map,
        district_categories=district_categories,
        trade_routes=trade_routes,
        current_session=current_session,
        editable=True,
        connectable_nations=connectable_nations,
        visibility_level=visibility_level,
        visibility_bypassed=visibility_bypassed,
    )
    _r10 = perf_counter()

    def _ms(a, b): return f"{(b-a)*1000:.0f}ms"
    current_app.logger.warning(
        "[EDIT_PAGE] %s | get_nation=%s dropdown_options=%s linked_objects=%s "
        "get_form=%s populate_linked=%s bulk_edit_opts=%s district_defs=%s "
        "trade_routes=%s visibility=%s render_template=%s | TOTAL=%s",
        item_ref,
        _ms(_r0, _r1), _ms(_r1, _r2), _ms(_r2, _r3),
        _ms(_r3, _r4), _ms(_r4, _r5), _ms(_r5, _r6), _ms(_r6, _r7),
        _ms(_r7, _r8), _ms(_r8, _r9), _ms(_r9, _r10),
        _ms(_r0, _r10),
    )

    return rendered


@nation_routes.route("/nations/edit/<item_ref>", methods=["GET"])
def edit_nation(item_ref):
    """Display nation edit form"""
    return _render_nation_edit(item_ref)

@nation_routes.route("/nations/edit/<item_ref>/request", methods=["POST"])
def nation_edit_request(item_ref):
    """Handle nation edit request"""
    schema, db, nation = get_data_on_item("nations", item_ref)

    visibility_level, _ = get_item_visibility(
        "nations", nation,
        user=g.user,
        view_access_level=g.view_access_level,
        is_non_player_admin=g.is_non_player_admin,
    )

    form = form_generator.get_form("nations", schema, formdata=request.form)

    # Validate CSRF token only; skip WTForms field-level validation entirely.
    # Hidden SelectFields (outside the player's visibility tier) always submit ""
    # which fails choice validation — but those fields are stripped before submission
    # anyway. jsonschema below validates the visible field values instead.
    from flask_wtf.csrf import validate_csrf as _validate_csrf, ValidationError as _CSRFError
    try:
        _validate_csrf(request.form.get('csrf_token', ''))
    except _CSRFError:
        flash("Security token expired. Please refresh and try again.")
        return redirect(f"/nations/edit/{item_ref}")

    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    form_data.pop('territory_types', None)  # read-only; managed by hex map sync
    form_data = strip_form_data_to_tier(form_data, visibility_level)
    if "name" in form_data:
        form_data["name"] = form_data.get("name", "").strip()

    # Process concessions field
    if 'concessions' in form_data:
        try:
            if isinstance(form_data['concessions'], str):
                form_data['concessions'] = json.loads(form_data['concessions'])
        except (json.JSONDecodeError, TypeError):
            form_data['concessions'] = {}

    # Deserialize upgrades JSON strings back to lists for each district
    for dist in form_data.get('districts', []):
        if isinstance(dist.get('upgrades'), str):
            try:
                dist['upgrades'] = json.loads(dist['upgrades'])
            except (json.JSONDecodeError, TypeError):
                dist['upgrades'] = []

    # Validate only the stripped fields (partial update — skip required-field check).
    valid, error = validate_form_with_jsonschema(form, schema, data=form_data, partial=True)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect(f"/nations/edit/{item_ref}")

    valid, error = _validate_tech_costs(form_data)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect(f"/nations/edit/{item_ref}")

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

    visibility_level, _ = get_item_visibility(
        "nations", nation,
        user=g.user,
        view_access_level=g.view_access_level,
        is_non_player_admin=g.is_non_player_admin,
    )
    is_partial = visibility_level < 4

    form = form_generator.get_form("nations", schema, formdata=request.form)

    # Validate CSRF only; skip WTForms field-level choice validation.
    # When visibility is filtered some SelectFields are hidden and submit "" which
    # isn't a valid choice.  jsonschema below validates the submitted values instead.
    from flask_wtf.csrf import validate_csrf as _validate_csrf, ValidationError as _CSRFError
    try:
        _validate_csrf(request.form.get('csrf_token', ''))
    except _CSRFError:
        flash("Security token expired. Please refresh and try again.")
        return redirect(f"/nations/edit/{item_ref}")

    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    form_data.pop('territory_types', None)  # read-only; managed by hex map sync
    if is_partial:
        form_data = strip_form_data_to_tier(form_data, visibility_level)
    if "name" in form_data:
        form_data["name"] = form_data.get("name", "").strip()

    # Process concessions field
    if 'concessions' in form_data:
        try:
            if isinstance(form_data['concessions'], str):
                form_data['concessions'] = json.loads(form_data['concessions'])
        except (json.JSONDecodeError, TypeError):
            form_data['concessions'] = {}

    # Deserialize upgrades JSON strings back to lists for each district
    for dist in form_data.get('districts', []):
        if isinstance(dist.get('upgrades'), str):
            try:
                dist['upgrades'] = json.loads(dist['upgrades'])
            except (json.JSONDecodeError, TypeError):
                dist['upgrades'] = []

    valid, error = validate_form_with_jsonschema(form, schema, data=form_data, partial=is_partial)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect(f"/nations/edit/{item_ref}")

    valid, error = _validate_tech_costs(form_data)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect(f"/nations/edit/{item_ref}")

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


# ---------------------------------------------------------------------------
# Nation cosmetics (accent color, banner, flag)
# ---------------------------------------------------------------------------

_NATION_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
_ACCENT_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')


def _is_nation_owner_or_admin(nation_id_str):
    if not g.user:
        return False
    user = mongo.db.players.find_one({"id": g.user.get("id")})
    if not user:
        return False
    if user.get("is_admin"):
        return True
    characters = list(mongo.db.characters.find({"player": str(user["_id"])}, {"ruling_nation_org": 1}))
    return any(str(c.get("ruling_nation_org", "")) == nation_id_str for c in characters)


@nation_routes.route("/nations/cosmetics/<item_ref>", methods=["GET"])
def nation_cosmetics(item_ref):
    schema, db, nation = get_data_on_item("nations", item_ref)
    if not _is_nation_owner_or_admin(str(nation["_id"])):
        flash("You don't have permission to customize this nation.")
        return redirect(f"/nations/item/{item_ref}")
    return render_template("nation_cosmetics.html", nation=nation)


@nation_routes.route("/nations/cosmetics/<item_ref>/save", methods=["POST"])
def nation_cosmetics_save(item_ref):
    schema, db, nation = get_data_on_item("nations", item_ref)
    if not _is_nation_owner_or_admin(str(nation["_id"])):
        flash("You don't have permission to customize this nation.")
        return redirect(f"/nations/item/{item_ref}")

    updates = {
        "banner_url": request.form.get("banner_url", "").strip(),
        "flag_url":   request.form.get("flag_url",   "").strip(),
    }
    accent = request.form.get("accent_color", "").strip()
    accent_changed = False
    if _ACCENT_COLOR_RE.match(accent):
        updates["accent_color"] = accent
        accent_changed = True

    mongo.db.nations.update_one({"_id": nation["_id"]}, {"$set": updates})
    if accent_changed:
        from helpers.hex_map_helpers import bump_tile_version
        bump_tile_version()
    flash("Customization saved.")
    return redirect(f"/nations/item/{nation.get('name', item_ref)}")


def _upload_nation_image(nation, image_type):
    if 'image' not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400
    file = request.files['image']
    if not file.filename:
        return jsonify({"success": False, "error": "No file selected"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in _NATION_IMAGE_EXTENSIONS:
        return jsonify({"success": False, "error": f"File type '{ext}' not allowed"}), 400

    safe_name = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in nation.get("name", "unknown").replace(" ", "_")
    ).lower()
    s3_key = f"nation_images/{safe_name}_{image_type}{ext}"

    file_bytes = file.read()
    success, result = upload_bytes_to_s3(file_bytes, s3_key, file.content_type or "image/jpeg")
    if success:
        return jsonify({"success": True, "url": result})
    return jsonify({"success": False, "error": result}), 500


@nation_routes.route("/nations/item/<path:nation_name>/district/<instance_id>/upgrade/<upgrade_key>", methods=["POST"])
def purchase_district_upgrade(nation_name, instance_id, upgrade_key):
    nation = mongo.db.nations.find_one({"name": nation_name})
    if not nation:
        return jsonify({"error": "Nation not found"}), 404

    if not _is_nation_owner_or_admin(str(nation["_id"])):
        return jsonify({"error": "Unauthorized"}), 403

    # Find the district instance
    district = next(
        (d for d in nation.get("districts", []) if isinstance(d, dict) and d.get("_id") == instance_id),
        None
    )
    if not district:
        return jsonify({"error": "District instance not found"}), 404

    if not district.get("def_key"):
        return jsonify({"error": "Upgrades are only available for DB-defined districts"}), 400

    district_def = _resolve_def(district)
    if not district_def:
        return jsonify({"error": "District definition not found"}), 404

    upgrade_def = next((u for u in district_def.get("upgrades", []) if u.get("key") == upgrade_key), None)
    if not upgrade_def:
        return jsonify({"error": "Upgrade not found"}), 404

    if upgrade_key in (district.get("upgrades") or []):
        return jsonify({"error": "Upgrade already purchased"}), 400

    if not check_upgrade_requirements(nation, upgrade_def):
        return jsonify({"error": "Requirements not met"}), 400

    # Deduct cost (same as district build cost)
    cost = district_def.get("cost", {})
    if cost:
        storage = nation.get("storage", {})
        for resource, amount in cost.items():
            if (storage.get(resource) or 0) < amount:
                return jsonify({"error": f"Insufficient {resource}"}), 400
        cost_deductions = {"$inc": {f"storage.{r}": -a for r, a in cost.items()}}
        mongo.db.nations.update_one({"_id": nation["_id"]}, cost_deductions)

    # Add upgrade key to the district instance
    mongo.db.nations.update_one(
        {"_id": nation["_id"], "districts._id": instance_id},
        {"$push": {"districts.$.upgrades": upgrade_key}}
    )

    return jsonify({"ok": True, "upgrade_key": upgrade_key})


@nation_routes.route("/nations/upload_banner/<item_ref>", methods=["POST"])
def nation_upload_banner(item_ref):
    schema, db, nation = get_data_on_item("nations", item_ref)
    if not _is_nation_owner_or_admin(str(nation["_id"])):
        return jsonify({"success": False, "error": "Not authorized"}), 403
    return _upload_nation_image(nation, "banner")


@nation_routes.route("/nations/upload_flag/<item_ref>", methods=["POST"])
def nation_upload_flag(item_ref):
    schema, db, nation = get_data_on_item("nations", item_ref)
    if not _is_nation_owner_or_admin(str(nation["_id"])):
        return jsonify({"success": False, "error": "Not authorized"}), 403
    return _upload_nation_image(nation, "flag")


@nation_routes.route("/market_prices")
def market_prices():
    """Show the current dynamic resource prices for every market."""
    from helpers.ai_decision_helpers import _base_prices
    markets_raw = list(mongo.db.markets.find({}, {"name": 1, "resource_prices": 1}).sort("name", ASCENDING))
    base = _base_prices()
    all_resources = sorted(base.keys())
    return render_template(
        "market_prices.html",
        markets=markets_raw,
        base_prices=base,
        all_resources=all_resources,
    )


@nation_routes.route("/visibility/toggle_bypass", methods=["POST"])
def toggle_visibility_bypass():
    if not (g.user and g.user.get("is_admin")):
        abort(403)
    page_url = request.form.get("page_url", "/")
    nation_name = request.form.get("nation_name", "")
    log_visibility_bypass(
        page_url=page_url,
        nation_name=nation_name,
        source="nation_view",
        user=g.user,
    )
    base_path = page_url.split("?")[0]
    return redirect(base_path + "?bypass_visibility=1")
