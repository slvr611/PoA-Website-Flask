from flask import Blueprint, render_template, request, redirect, flash, jsonify, g
from forms import form_generator
from helpers.data_helpers import get_data_on_category, get_data_on_item
from helpers.change_helpers import request_change, approve_change, recalculate_object
from helpers.render_helpers import get_linked_objects
from helpers.form_helpers import validate_form_with_jsonschema
from routes.nation_routes import edit_nation, nation_edit_request, nation_edit_approve
from app_core import category_data, mongo, rarity_rankings, json_data, find_dict_in_list, upload_bytes_to_s3
from helpers.auth_helpers import admin_required
from helpers.visibility_helpers import gate_item_view, gate_item_edit, ITEM_VIEW_FIELD_TIERS
from calculations.field_calculations import calculate_all_fields
from pymongo import ASCENDING
from bson import ObjectId
from copy import deepcopy
import os


_DEMOGRAPHIC_TYPES = frozenset(("races", "cultures", "religions"))
_VISIBILITY_GATED_TYPES = frozenset({"characters", "artifacts"})


def _get_visible_pop_nations():
    """
    Return the set of nation names whose pops the current user may see in detail
    (visibility tier ≥ 1), or None if the user sees all pops (admin / non-player-admin).
    Returns an empty set when the user has no ruling nation.
    """
    from calculations.visibility import get_viewer_nation, compute_all_visibilities

    if getattr(g, "view_access_level", 0) >= 7:
        return None  # admin / non-player-admin: no filtering

    viewer_nation = get_viewer_nation(g.user)
    if not viewer_nation:
        return set()  # no nation → can't see any pops in detail

    visibility_map = compute_all_visibilities(viewer_nation)
    return {name for name, tier in visibility_map.items() if tier >= 1}


data_item_routes = Blueprint("data_item_routes", __name__)

_DISTRICT_FILES = [
    "nation_imperial_districts", "mercenary_districts",
    "merchant_production_districts", "merchant_specialty_districts", "merchant_luxury_districts"
]

def _build_unit_edit_extras(item=None):
    """Returns context variables shared by the new-unit and edit-unit routes."""
    all_traits = list(category_data["traits"]["database"].find(
        {}, {"_id": 0, "name": 1, "cost": 1, "description": 1}
    ).sort("name", ASCENDING))

    tech_options = sorted(
        [{"key": k, "display_name": v.get("display_name", k)} for k, v in json_data.get("tech", {}).items()],
        key=lambda x: x["display_name"]
    )

    # Build district key→category lookup and sorted category list
    district_key_to_cat = {}
    cat_set = set()
    for fname in _DISTRICT_FILES:
        for key, data in json_data.get(fname, {}).items():
            cat = data.get("type", "")
            if cat:
                district_key_to_cat[key] = cat
                cat_set.add(cat)
    district_categories = sorted(
        [{"key": c, "label": c.replace("_", " ").title()} for c in cat_set],
        key=lambda x: x["label"]
    )

    def _names(col):
        return [d["name"] for d in category_data[col]["database"].find({}, {"_id": 0, "name": 1}).sort("name", ASCENDING)]

    # Migrate any era-specific district keys to their category in-place
    if item and "prerequisites" in item:
        for pre in item["prerequisites"]:
            if pre.get("type") == "district":
                val = pre.get("value", "")
                if val in district_key_to_cat:
                    pre["value"] = district_key_to_cat[val]

    races_options = _names("races")

    return dict(
        all_traits=all_traits,
        tech_options=tech_options,
        district_categories=district_categories,
        nations_options=_names("nations"),
        artifacts_options=_names("artifacts"),
        spells_options=_names("spells"),
        mercenaries_options=_names("mercenaries"),
        races_options=races_options,
    )



ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}

@data_item_routes.route("/units/upload_image", methods=["POST"])
@admin_required
def unit_upload_image():
    if 'image' not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400
    file = request.files['image']
    if not file.filename:
        return jsonify({"success": False, "error": "No file selected"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return jsonify({"success": False, "error": f"File type '{ext}' not allowed"}), 400

    unit_name = request.form.get("unit_name", "unknown").strip()
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in unit_name.replace(" ", "_")).lower()
    s3_key = f"unit_images/{safe_name}{ext}"

    file_bytes = file.read()
    content_type = file.content_type or "image/jpeg"

    success, result = upload_bytes_to_s3(file_bytes, s3_key, content_type)
    if success:
        return jsonify({"success": True, "url": result})
    else:
        return jsonify({"success": False, "error": result}), 500

@data_item_routes.route("/races/upload_image", methods=["POST"])
@admin_required
def race_upload_image():
    if 'image' not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400
    file = request.files['image']
    if not file.filename:
        return jsonify({"success": False, "error": "No file selected"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return jsonify({"success": False, "error": f"File type '{ext}' not allowed"}), 400

    item_name = request.form.get("item_name", "unknown").strip()
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in item_name.replace(" ", "_")).lower()
    s3_key = f"race_images/{safe_name}{ext}"

    file_bytes = file.read()
    content_type = file.content_type or "image/jpeg"

    success, result = upload_bytes_to_s3(file_bytes, s3_key, content_type)
    if success:
        return jsonify({"success": True, "url": result})
    else:
        return jsonify({"success": False, "error": result}), 500

@data_item_routes.route("/api/wonders/default-image")
def wonder_default_image():
    """Returns {url} for the global default wonder map icon."""
    doc = mongo.db.global_modifiers.find_one({"name": "wonder_default_image"}) or {}
    return jsonify({"url": doc.get("url", "")})


@data_item_routes.route("/wonders/upload-default-image", methods=["POST"])
@admin_required
def wonder_upload_default_image():
    if "image" not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400
    file = request.files["image"]
    if not file.filename:
        return jsonify({"success": False, "error": "No file selected"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return jsonify({"success": False, "error": f"File type '{ext}' not allowed"}), 400

    file_bytes   = file.read()
    content_type = file.content_type or "image/jpeg"

    success, result = upload_bytes_to_s3(file_bytes, f"wonder_images/default{ext}", content_type)
    if success:
        mongo.db.global_modifiers.update_one(
            {"name": "wonder_default_image"}, {"$set": {"url": result}}, upsert=True
        )
        return jsonify({"success": True, "url": result})
    else:
        return jsonify({"success": False, "error": result}), 500


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

    visibility_bypassed = None  # None = no visibility gating for this data type

    if data_type == "artifacts":
        visibility_bypassed = _apply_artifact_list_visibility(
            items, preview_overall_lookup_dict
        )

    return render_template(
        "dataList.html",
        title=category_data[data_type]["pluralName"],
        items=items,
        schema=schema,
        preview_references=preview_overall_lookup_dict,
        visibility_bypassed=visibility_bypassed,
    )


def _apply_artifact_list_visibility(items, preview_references):
    """
    Compute visibility for the artifact list owner column.

    Mutates preview_references["owner"] in-place: for owner characters whose
    ruling nation falls below tier 2 for the current viewer, replaces the
    owner display with the character's region name.

    Returns visibility_bypassed (True/False/None per the standard convention).
    Non-player-admin → True (auto-bypass, no log).
    Admin with ?bypass_visibility=1 → True (logged).
    Otherwise → False (filtered).
    """
    from helpers.visibility_helpers import log_visibility_bypass, ITEM_VIEW_FIELD_TIERS
    from calculations.visibility import get_viewer_nation, compute_all_visibilities

    explicit_bypass = bool(
        g.user and g.user.get("is_admin")
        and request.args.get("bypass_visibility") == "1"
    )
    non_player_admin = getattr(g, "is_non_player_admin", False)
    visibility_bypassed = explicit_bypass or non_player_admin

    if explicit_bypass and not non_player_admin:
        log_visibility_bypass(
            page_url=request.url,
            nation_name="",
            source="artifact_list",
            user=g.user,
        )

    if visibility_bypassed:
        return visibility_bypassed

    owner_tier_required = ITEM_VIEW_FIELD_TIERS["artifacts"].get("owner", 0)

    # Build nation_id → visibility tier for the current viewer
    viewer_nation = get_viewer_nation(g.user) if g.user else None
    if viewer_nation:
        name_to_tier = compute_all_visibilities(viewer_nation)
        nation_id_to_tier = {
            str(nation["_id"]): name_to_tier.get(nation.get("name", ""), 0)
            for nation in mongo.db.nations.find({}, {"_id": 1, "name": 1})
        }
    else:
        nation_id_to_tier = {}

    # Collect unique owner character IDs across all items
    owner_oids = []
    for item in items:
        raw = item.get("owner")
        if raw:
            try:
                owner_oids.append(ObjectId(str(raw)))
            except Exception:
                pass

    if not owner_oids:
        return visibility_bypassed

    # Fetch each owner character's ruling nation and region in one query
    chars = {}
    for char in mongo.db.characters.find(
        {"_id": {"$in": owner_oids}},
        {"ruling_nation_org": 1, "region": 1},
    ):
        chars[str(char["_id"])] = char

    # Fetch region names for the characters that need them
    region_oids = []
    for char in chars.values():
        raw = char.get("region")
        if raw:
            try:
                region_oids.append(ObjectId(str(raw)))
            except Exception:
                pass

    region_name_by_id = {}
    if region_oids:
        for region in mongo.db.regions.find(
            {"_id": {"$in": region_oids}},
            {"name": 1},
        ):
            region_name_by_id[str(region["_id"])] = region.get("name", "Unknown Region")

    # Override preview_references["owner"] for owners below the required tier
    owner_refs = preview_references.setdefault("owner", {})
    for owner_id_str, char in chars.items():
        nation_raw = char.get("ruling_nation_org")
        if not nation_raw:
            # No ruling nation → artifact is publicly visible → keep owner display
            continue
        nation_id = str(nation_raw)
        tier = nation_id_to_tier.get(nation_id, 0)
        if tier >= owner_tier_required:
            continue

        region_raw = char.get("region")
        region_id = str(region_raw) if region_raw else ""
        region_name = region_name_by_id.get(region_id, "Unknown Region")
        owner_refs[owner_id_str] = {
            "name": region_name,
            "link": f"regions/item/{region_name}" if region_name != "Unknown Region" else "",
        }

    return visibility_bypassed

@data_item_routes.route("/<data_type>/item/<item_ref>")
def data_item(data_type, item_ref):
    schema, db, item = get_data_on_item(data_type, item_ref)
    linked_objects = get_linked_objects(schema, item)

    if data_type in _VISIBILITY_GATED_TYPES:
        visibility_level, visibility_bypassed = gate_item_view(
            data_type, item,
            user=g.user,
            view_access_level=g.view_access_level,
            is_non_player_admin=g.is_non_player_admin,
        )
        field_tiers = ITEM_VIEW_FIELD_TIERS.get(data_type, {})
    else:
        visibility_level = None
        visibility_bypassed = False
        field_tiers = None

    if data_type in _DEMOGRAPHIC_TYPES and "pops" in linked_objects:
        visible_nations = _get_visible_pop_nations()
        if visible_nations is not None:
            linked_objects["pops"] = [
                p for p in linked_objects["pops"]
                if p.get("nation") in visible_nations
            ]

    unit_traits = []
    if data_type == "units":
        trait_names = item.get("traits", [])
        if trait_names:
            traits_db = category_data["traits"]["database"]
            unit_traits = list(traits_db.find({"name": {"$in": trait_names}}))
            trait_order = {name: i for i, name in enumerate(trait_names)}
            unit_traits.sort(key=lambda t: trait_order.get(t.get("name"), 999))

    template_name = "units_item.html" if data_type == "units" else "dataItem.html"

    schema_props = schema.get("properties", {})
    breakdowns = {}
    if any(v.get("show_breakdown") for v in schema_props.values() if isinstance(v, dict)):
        singular_type = category_data[data_type]["singularName"].lower()
        calculated_values, breakdowns = calculate_all_fields(
            item, schema, singular_type, return_breakdowns=True
        )
        item.update(calculated_values)

    district_files = ["nation_imperial_districts", "mercenary_districts",
                      "merchant_production_districts", "merchant_specialty_districts", "merchant_luxury_districts"]
    districts_lookup = {}
    for fname in district_files:
        for key, data in json_data.get(fname, {}).items():
            districts_lookup[key] = data.get("display_name", key)

    tech_lookup = {key: data.get("display_name", key) for key, data in json_data.get("tech", {}).items()}

    def _names_set(col):
        return {d["name"] for d in category_data[col]["database"].find({}, {"_id": 0, "name": 1})}

    return render_template(
        template_name,
        title=item.get("name", str(item.get("_id", ""))),
        schema=schema,
        item=item,
        linked_objects=linked_objects,
        json_data=json_data,
        find_dict_in_list=find_dict_in_list,
        unit_traits=unit_traits,
        districts_lookup=districts_lookup,
        tech_lookup=tech_lookup,
        mercenaries_names=_names_set("mercenaries"),
        races_names=_names_set("races"),
        breakdowns=breakdowns,
        visibility_level=visibility_level,
        visibility_bypassed=visibility_bypassed,
        field_tiers=field_tiers,
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

    if data_type == "units":
        extras = _build_unit_edit_extras(item=None)
        return render_template(
            "units_edit.html",
            title="New Unit",
            schema=schema,
            form=form,
            item=None,
            dropdown_options=dropdown_options,
            **extras
        )

    SOURCE_TYPE_MAP = {
        "characters": "character", "nations": "nation", "artifacts": "artifact",
        "merchants": "merchant", "mercenaries": "mercenary",
        "laws": "nation", "titles": "character",
        "wonders": "wonder", "regions": "region", "global_modifiers": "global",
    }
    return render_template(
        "dataItemNew.html",
        title="New " + category_data[data_type]["singularName"],
        schema=schema,
        form=form,
        dropdown_options=dropdown_options,
        entity_source_type=SOURCE_TYPE_MAP.get(data_type, "")
    )

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
    if "name" in form_data:
        form_data["name"] = form_data.get("name", "").strip()
    
    # Additional JSON schema validation
    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect("/" + data_type + "/new")
    
    if "name" in form_data:
        if data_type == "units":
            if db.find_one({"name": form_data["name"], "era": form_data.get("era")}):
                flash("A unit with this name already exists in this era!")
                return redirect("/units/new")
        elif db.find_one({"name": form_data["name"]}):
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
    if "name" in form_data:
        form_data["name"] = form_data.get("name", "").strip()
    
    # Additional JSON schema validation
    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect("/" + data_type + "/new")
    
    if "name" in form_data:
        if data_type == "units":
            if db.find_one({"name": form_data["name"], "era": form_data.get("era")}):
                flash("A unit with this name already exists in this era!")
                return redirect("/units/new")
        elif db.find_one({"name": form_data["name"]}):
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

    if data_type in _VISIBILITY_GATED_TYPES:
        result = gate_item_edit(
            data_type, item,
            user=g.user,
            view_access_level=g.view_access_level,
            is_non_player_admin=g.is_non_player_admin,
        )
        if result is not True:
            return result

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

    form = form_generator.get_form(data_type, schema, item=item)
    form.populate_linked_fields(schema, dropdown_options)

    if data_type == "units":
        extras = _build_unit_edit_extras(item=item)
        return render_template(
            "units_edit.html",
            title=f"Edit {item_ref}",
            schema=schema,
            form=form,
            item=item,
            dropdown_options=dropdown_options,
            **extras
        )

    SOURCE_TYPE_MAP = {
        "characters": "character", "nations": "nation", "artifacts": "artifact",
        "merchants": "merchant", "mercenaries": "mercenary",
        "laws": "nation", "titles": "character",
        "wonders": "wonder", "regions": "region", "global_modifiers": "global",
    }
    return render_template(
        "dataItemEdit.html",
        title=f"Edit {item_ref}",
        schema=schema,
        form=form,
        item=item,
        dropdown_options=dropdown_options,
        entity_source_type=SOURCE_TYPE_MAP.get(data_type, ""),
        data_type=data_type,
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
    if "name" in form_data:
        form_data["name"] = form_data.get("name", "").strip()
    
    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect("/" + data_type + "/edit/" + item_ref)
    
    if "name" in form_data:
        if data_type == "units":
            if db.find_one({"name": form_data["name"], "era": form_data.get("era"), "_id": {"$ne": item["_id"]}}):
                flash("A unit with this name already exists in this era!")
                return redirect(f"/units/edit/{item_ref}")
        elif form_data["name"] != item.get("name") and db.find_one({"name": form_data["name"]}):
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
    if "name" in form_data:
        form_data["name"] = form_data.get("name", "").strip()
    
    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
        return redirect("/" + data_type + "/edit/" + item_ref)
    
    if "name" in form_data:
        if data_type == "units":
            if db.find_one({"name": form_data["name"], "era": form_data.get("era"), "_id": {"$ne": item["_id"]}}):
                flash("A unit with this name already exists in this era!")
                return redirect(f"/units/edit/{item_ref}")
        elif form_data["name"] != item.get("name") and db.find_one({"name": form_data["name"]}):
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

@data_item_routes.route("/<data_type>/item/<item_ref>/recalculate", methods=["GET"])
def data_item_recalculate(data_type, item_ref):
    recalculate_object(data_type, item_ref)
    flash(f"Recalculated {data_type} {item_ref}")
    return redirect("/" + data_type + "/item/" + item_ref)

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
    chart_labels = [item[0] for item in sorted_race_data]
    chart_values = [item[1] for item in sorted_race_data]

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
        chart_labels=chart_labels,
        chart_values=chart_values,
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
    chart_labels = [item[0] for item in sorted_culture_data]
    chart_values = [item[1] for item in sorted_culture_data]

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
        chart_labels=chart_labels,
        chart_values=chart_values,
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
    chart_labels = [item[0] for item in sorted_religion_data]
    chart_values = [item[1] for item in sorted_religion_data]

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
        chart_labels=chart_labels,
        chart_values=chart_values,
        pop_counts=pop_counts
    )

@data_item_routes.route("/markets/item/<item_ref>")
def market_item(item_ref):
    schema, db, market = get_data_on_item("markets", item_ref)
    linked_objects = get_linked_objects(schema, market)
    
    # Get all nations that are members of this market
    member_nations = []
    market_links_db = category_data["market_links"]["database"]
    market_links = list(market_links_db.find({"market": str(market["_id"])}, {"member": 1}))
    market["members"] = [link["member"] for link in market_links]
    nations_db = category_data["nations"]["database"]
    for member_id in market["members"]:
        nation = nations_db.find_one({"_id": ObjectId(member_id)})
        if nation:
            member_nations.append(nation)
    
    # Collect resource desires from all member nations
    resource_desires = []
    for nation in member_nations:
        if "resource_desires" in nation:
            for desire in nation.get("resource_desires", []):
                # Only include desires with quantity > 0
                if desire.get("quantity", 0) > 0:
                    resource_desires.append({
                        "nation": nation.get("name", "Unknown"),
                        "nation_id": str(nation.get("_id", "")),
                        "resource": desire.get("resource", "Unknown"),
                        "trade_type": desire.get("trade_type", "Unknown"),
                        "price": desire.get("price", 0),
                        "quantity": desire.get("quantity", 0)
                    })
    
    # Sort by resource, then by price
    resource_desires.sort(key=lambda x: (x["resource"], x["price"]))

    return render_template(
        "marketItem.html",
        title=market.get("name", str(market.get("_id", ""))),
        schema=schema,
        item=market,
        linked_objects=linked_objects,
        resource_desires=resource_desires,
        json_data=json_data,
        find_dict_in_list=find_dict_in_list
    )

@data_item_routes.route("/markets/trade/request", methods=["POST"])
def market_trade_request():
    nation_id = request.form.get("nation_id")
    resource = request.form.get("resource")
    trade_type = request.form.get("trade_type")
    price = int(request.form.get("price", 0))
    quantity = int(request.form.get("quantity", 0))
    reason = request.form.get("reason", "No reason provided")
    
    # Get the nation
    nations_db = category_data["nations"]["database"]
    nation = nations_db.find_one({"_id": ObjectId(nation_id)})
    
    if not nation:
        flash("Nation not found")
        return redirect("/go_back")
    
    # Create a copy of the nation for before/after comparison
    before_data = deepcopy(nation)
    after_data = deepcopy(nation)
    
    # Find and update the resource desire
    for i, desire in enumerate(after_data.get("resource_desires", [])):
        if desire.get("resource") == resource and desire.get("trade_type") == trade_type and desire.get("price") == price:
            # Calculate new quantity
            current_quantity = desire.get("quantity", 0)
            new_quantity = max(0, current_quantity - quantity)
            
            # Update the quantity
            after_data["resource_desires"][i]["quantity"] = new_quantity
            break
    
    # Create change request
    change_id = request_change(
        data_type="nations",
        item_id=nation["_id"],
        change_type="Update",
        before_data=before_data,
        after_data=after_data,
        reason=f"Trade request: {trade_type} {quantity} {resource} at {price} gold each. {reason}"
    )
    
    flash(f"Trade request #{change_id} created and awaits admin approval.")
    return redirect(request.referrer or "/markets")

@data_item_routes.route("/markets/trade/save", methods=["POST"])
@admin_required
def market_trade_save():
    nation_id = request.form.get("nation_id")
    resource = request.form.get("resource")
    trade_type = request.form.get("trade_type")
    price = int(request.form.get("price", 0))
    quantity = int(request.form.get("quantity", 0))
    reason = request.form.get("reason", "No reason provided")
    
    # Get the nation
    nations_db = category_data["nations"]["database"]
    nation = nations_db.find_one({"_id": ObjectId(nation_id)})
    
    if not nation:
        flash("Nation not found")
        return redirect("/go_back")
    
    # Create a copy of the nation for before/after comparison
    before_data = deepcopy(nation)
    after_data = deepcopy(nation)
    
    # Find and update the resource desire
    for i, desire in enumerate(after_data.get("resource_desires", [])):
        if desire.get("resource") == resource and desire.get("trade_type") == trade_type and desire.get("price") == price:
            # Calculate new quantity
            current_quantity = desire.get("quantity", 0)
            new_quantity = max(0, current_quantity - quantity)
            
            # Update the quantity
            after_data["resource_desires"][i]["quantity"] = new_quantity
            break
    
    # Create and approve change request
    change_id = request_change(
        data_type="nations",
        item_id=nation["_id"],
        change_type="Update",
        before_data=before_data,
        after_data=after_data,
        reason=f"Trade executed: {trade_type} {quantity} {resource} at {price} gold each. {reason}"
    )
    
    approve_change(change_id)
    
    flash(f"Trade request #{change_id} created and approved.")
    return redirect(request.referrer or "/markets")
