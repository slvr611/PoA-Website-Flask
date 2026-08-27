from flask import Blueprint, render_template, request, redirect, flash, jsonify, g, url_for
from forms import form_generator
from helpers.data_helpers import get_data_on_category, get_data_on_item
from helpers.change_helpers import request_change, approve_change, recalculate_object
from helpers.render_helpers import get_linked_objects
from helpers.form_helpers import validate_form_with_jsonschema
from routes.nation_routes import edit_nation, nation_edit_request, nation_edit_approve
from app_core import category_data, mongo, rarity_rankings, json_data, find_dict_in_list, upload_bytes_to_s3
from helpers.auth_helpers import admin_required
from helpers.visibility_helpers import gate_item_view, gate_item_edit, ITEM_VIEW_FIELD_TIERS
from helpers.data_item_view_helpers import build_item_view
from calculations.field_calculations import calculate_all_fields
from pymongo import ASCENDING
from bson import ObjectId
from copy import deepcopy
import os


_DEMOGRAPHIC_TYPES = frozenset(("races", "cultures", "religions"))
_VISIBILITY_GATED_TYPES = frozenset({"characters", "artifacts", "merchants"})

# Data types where even the "request a change, pending admin approval" flow
# is admin-only — unlike nations/artifacts/etc., where any player can
# request an edit for review, units are static game-balance content, not
# player-driven data, so no non-admin should reach the edit form or be able
# to submit a change request at all.
_ADMIN_ONLY_EDIT_TYPES = frozenset({"units"})


def _get_visible_pop_nations():
    """
    Return the set of nation names whose pops the current user may see in detail
    (visibility tier ≥ 1), or None if the user sees all pops (admin / non-player-admin).
    Returns an empty set when the user has no ruling nation.
    """
    from calculations.visibility import get_viewer_nations, compute_all_visibilities

    if getattr(g, "view_access_level", 0) >= 7:
        return None  # admin / non-player-admin: no filtering

    viewer_nations = get_viewer_nations(g.user)
    if not viewer_nations:
        return set()  # no nation → can't see any pops in detail

    visibility_map = compute_all_visibilities(viewer_nations)
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

    # District options from DB: categories + individual district defs
    district_categories_raw = list(mongo.db.district_categories.find(
        {}, {"key": 1, "display_name": 1, "_id": 0}
    ).sort("display_name", ASCENDING))
    district_defs_raw = list(mongo.db.district_defs.find(
        {}, {"key": 1, "display_name": 1, "category": 1, "tier": 1, "_id": 0}
    ).sort("display_name", ASCENDING))
    district_options = []
    for cat in district_categories_raw:
        district_options.append({
            "key": cat["key"],
            "label": cat.get("display_name", cat["key"]),
            "group": "Categories",
        })
    for d in district_defs_raw:
        cat_name = d.get("category", "")
        cat_label = next(
            (c.get("display_name", c["key"]) for c in district_categories_raw if c["key"] == cat_name),
            cat_name,
        )
        district_options.append({
            "key": d["key"],
            "label": d.get("display_name", d["key"]),
            "group": cat_label or "Other",
            "tier": d.get("tier", 1),
        })

    def _names(col):
        return [d["name"] for d in category_data[col]["database"].find({}, {"_id": 0, "name": 1}).sort("name", ASCENDING)]

    races_options = _names("races")

    unique_resource_keys = [r["key"] for r in json_data.get("unique_resources", [])]

    return dict(
        all_traits=all_traits,
        tech_options=tech_options,
        district_options=district_options,
        nations_options=_names("nations"),
        artifacts_options=_names("artifacts"),
        spells_options=_names("spells"),
        mercenaries_options=_names("mercenaries"),
        races_options=races_options,
        unique_resource_keys=unique_resource_keys,
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


@data_item_routes.route("/api/capitals/default-image")
def capital_default_image():
    """Returns {url} for the global capital map icon."""
    doc = mongo.db.global_modifiers.find_one({"name": "capital_default_image"}) or {}
    return jsonify({"url": doc.get("url", "")})


@data_item_routes.route("/capitals/upload-default-image", methods=["POST"])
@admin_required
def capital_upload_default_image():
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

    success, result = upload_bytes_to_s3(file_bytes, f"capital_images/default{ext}", content_type)
    if success:
        mongo.db.global_modifiers.update_one(
            {"name": "capital_default_image"}, {"$set": {"url": result}}, upsert=True
        )
        return jsonify({"success": True, "url": result})
    else:
        return jsonify({"success": False, "error": result}), 500


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

    if data_type == "artifacts":
        query_dict["archived"] = 1

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
        active_artifacts = [a for a in items if not a.get("archived")]
        archived_artifacts = [a for a in items if a.get("archived")]
        return render_template(
            "artifact_list.html",
            title=category_data["artifacts"]["pluralName"],
            active_artifacts=active_artifacts,
            archived_artifacts=archived_artifacts,
            schema=schema,
            preview_references=preview_overall_lookup_dict,
            visibility_bypassed=visibility_bypassed,
        )

    if data_type == "wars":
        all_wars = list(mongo.db.wars.find(
            {}, {"name": 1, "war_type": 1, "primary_aggressor": 1, "primary_defender": 1,
                 "session_declared": 1, "session_ended": 1, "victor": 1}
        ).sort("name", ASCENDING))

        nation_ids = set()
        for w in all_wars:
            for field in ("primary_aggressor", "primary_defender"):
                if w.get(field):
                    nation_ids.add(w[field])
        nation_names = {}
        for nid in nation_ids:
            try:
                n = mongo.db.nations.find_one({"_id": ObjectId(nid)}, {"name": 1})
            except Exception:
                n = None
            nation_names[nid] = n.get("name", "Unknown") if n else "Unknown"
        war_types = json_data.get("war_types", {})
        for w in all_wars:
            w["aggressor_name"] = nation_names.get(w.get("primary_aggressor", ""), "Unknown")
            w["defender_name"] = nation_names.get(w.get("primary_defender", ""), "Unknown")
            w["war_type_display"] = war_types.get(w.get("war_type", ""), {}).get("display_name", w.get("war_type", ""))

        active_wars = [w for w in all_wars if w.get("session_ended") is None]
        archived_wars = [w for w in all_wars if w.get("session_ended") is not None]
        return render_template(
            "war_list.html",
            title=category_data["wars"]["pluralName"],
            active_wars=active_wars,
            archived_wars=archived_wars,
        )

    return render_template(
        "dataList.html",
        title=category_data[data_type]["pluralName"],
        items=items,
        schema=schema,
        preview_references=preview_overall_lookup_dict,
        visibility_bypassed=visibility_bypassed,
    )


@data_item_routes.route("/artifacts/bulk-archive", methods=["POST"])
@admin_required
def bulk_archive_artifacts():
    artifact_ids = request.form.getlist("artifact_ids")
    if artifact_ids:
        object_ids = []
        for aid in artifact_ids:
            try:
                object_ids.append(ObjectId(aid))
            except Exception:
                pass
        if object_ids:
            mongo.db.artifacts.update_many(
                {"_id": {"$in": object_ids}},
                {"$set": {"archived": True}}
            )
            flash(f"Archived {len(object_ids)} artifact(s).")
    return redirect("/artifacts")


@data_item_routes.route("/artifacts/bulk-unarchive", methods=["POST"])
@admin_required
def bulk_unarchive_artifacts():
    artifact_ids = request.form.getlist("artifact_ids")
    if artifact_ids:
        object_ids = []
        for aid in artifact_ids:
            try:
                object_ids.append(ObjectId(aid))
            except Exception:
                pass
        if object_ids:
            mongo.db.artifacts.update_many(
                {"_id": {"$in": object_ids}},
                {"$set": {"archived": False}}
            )
            flash(f"Unarchived {len(object_ids)} artifact(s).")
    return redirect("/artifacts")


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
    from calculations.visibility import get_viewer_nations, compute_all_visibilities

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
    viewer_nations = get_viewer_nations(g.user) if g.user else []
    if viewer_nations:
        name_to_tier = compute_all_visibilities(viewer_nations)
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

    primary_nations = []
    if data_type in _DEMOGRAPHIC_TYPES:
        item_id = str(item.get("_id", ""))
        field_map = {"races": "primary_race", "cultures": "primary_culture", "religions": "primary_religion"}
        primary_field = field_map.get(data_type)
        if primary_field and item_id:
            nations_db = category_data["nations"]["database"]
            for n in nations_db.find(
                {primary_field: item_id},
                {"name": 1, "pop_count": 1},
            ).sort("name", ASCENDING):
                primary_nations.append({
                    "name": n.get("name", ""),
                    "pop_count": n.get("pop_count", 0),
                })

    unit_traits = []
    if data_type == "units":
        trait_names = item.get("traits", [])
        if trait_names:
            traits_db = category_data["traits"]["database"]
            unit_traits = list(traits_db.find({"name": {"$in": trait_names}}))
            trait_order = {name: i for i, name in enumerate(trait_names)}
            unit_traits.sort(key=lambda t: trait_order.get(t.get("name"), 999))

    disease_extras = {}
    if data_type == "diseases":
        from helpers.disease_helpers import (
            get_infectivity_settings, get_difficulty_settings, get_accepted_counts_by_nation,
        )
        infected_rows = []
        total_infected = 0
        nation_ids = []
        counts_by_nation = {}
        for doc in mongo.db.pops.aggregate([
            {"$match": {"diseases": str(item["_id"])}},
            {"$group": {"_id": "$nation", "count": {"$sum": 1}}},
        ]):
            counts_by_nation[str(doc["_id"])] = doc["count"]
            total_infected += doc["count"]
            try:
                nation_ids.append(ObjectId(str(doc["_id"])))
            except Exception:
                pass
        # Accepted pops (permanently converted to the disease's derived race,
        # disease marker cleared) are still hosts of the disease but invisible
        # to the query above — add them per-nation so both the total and the
        # breakdown table match the cure-progress gating check's real count.
        accepted_by_nation = get_accepted_counts_by_nation(item)
        for nid, count in accepted_by_nation.items():
            counts_by_nation[nid] = counts_by_nation.get(nid, 0) + count
            total_infected += count
            try:
                nation_ids.append(ObjectId(nid))
            except Exception:
                pass
        nation_names = {str(n["_id"]): n.get("name", "") for n in
                        mongo.db.nations.find({"_id": {"$in": nation_ids}}, {"name": 1})}
        for nid, count in sorted(counts_by_nation.items(), key=lambda kv: -kv[1]):
            infected_rows.append({"nation": nation_names.get(nid, nid), "count": count})
        disease_extras = {
            "infectivity_settings": get_infectivity_settings(item),
            "difficulty_settings": get_difficulty_settings(item),
            "infected_rows": infected_rows,
            "total_infected": total_infected,
            "all_nations": list(mongo.db.nations.find({}, {"name": 1}).sort("name", ASCENDING)),
        }

    template_name = "units_item.html" if data_type == "units" else "dataItem.html"
    if data_type == "diseases":
        template_name = "diseases_item.html"

    schema_props = schema.get("properties", {})
    breakdowns = {}
    # Merchants always recompute on view (rather than relying on a
    # show_breakdown flag) so resource_production/resource_capacity stay
    # accurate between session ticks instead of showing stale stored values.
    if data_type == "merchants" or any(v.get("show_breakdown") for v in schema_props.values() if isinstance(v, dict)):
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

    item_view = None
    if template_name == "dataItem.html":
        item_view = build_item_view(
            schema, item, breakdowns, linked_objects, json_data,
            field_tiers, visibility_level, visibility_bypassed, g.view_access_level,
            find_dict_in_list, json_data.get("scope_definitions", {}),
        )

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
        primary_nations=primary_nations,
        item_view=item_view,
        **disease_extras,
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

_SOURCE_TYPE_MAP = {
    "characters": "character", "nations": "nation", "artifacts": "artifact",
    "merchants": "merchant", "mercenaries": "mercenary",
    "laws": "nation", "titles": "character",
    "wonders": "wonder", "regions": "region", "global_modifiers": "global",
}


def _build_dropdown_options(schema):
    dropdown_options = {}
    for field, attributes in schema["properties"].items():
        dropdown_options[field] = []
        if attributes.get("collections") is not None:
            for related_collection in attributes.get("collections"):
                dropdown_options[field] += list(
                    mongo.db[related_collection].find({}, {"name": 1, "_id": 1}).sort("name", ASCENDING)
                )
    return dropdown_options


def _render_item_form(data_type, schema, form, item=None, item_ref=None, title=None):
    """Render the new/edit form for a data_type (units, diseases, or the
    generic dataItem), given an already-built `form`.

    Shared by the GET new/edit routes and by the POST request/save routes'
    validation-failure branches — passing the form built from the submitted
    request.form re-renders it with the user's entered values intact,
    instead of redirecting back to a blank form.
    """
    dropdown_options = _build_dropdown_options(schema)
    form.populate_linked_fields(schema, dropdown_options)

    if title is None:
        title = f"Edit {item_ref}" if item_ref else "New " + category_data[data_type]["singularName"]

    if data_type == "units":
        extras = _build_unit_edit_extras(item=item)
        return render_template(
            "units_item.html",
            title=title,
            schema=schema,
            form=form,
            item=item,
            dropdown_options=dropdown_options,
            editable=True,
            **extras
        )

    if data_type == "diseases":
        return render_template(
            "diseases_item.html",
            title=title,
            schema=schema,
            form=form,
            item=item,
            editable=True,
        )

    return render_template(
        "dataItem.html",
        title=title,
        schema=schema,
        form=form,
        item=item,
        dropdown_options=dropdown_options,
        entity_source_type=_SOURCE_TYPE_MAP.get(data_type, ""),
        data_type=data_type,
        editable=True,
    )


@data_item_routes.route("/<data_type>/new", methods=["GET"])
def data_item_new(data_type):
    schema, db = get_data_on_category(data_type)
    form = form_generator.get_form(data_type, schema)
    return _render_item_form(data_type, schema, form, item=None)

@data_item_routes.route("/<data_type>/new/request", methods=["POST"])
def data_item_new_request(data_type):
    schema, db = get_data_on_category(data_type)

    dropdown_options = _build_dropdown_options(schema)
    form = form_generator.get_form(data_type, schema, formdata=request.form)
    form.populate_linked_fields(schema, dropdown_options)

    if not form.validate():
        flash("Form validation failed!")
        return _render_item_form(data_type, schema, form, item=None)

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
        return _render_item_form(data_type, schema, form, item=None)

    if "name" in form_data:
        if data_type == "units":
            if db.find_one({"name": form_data["name"], "era": form_data.get("era")}):
                flash("A unit with this name already exists in this era!")
                return _render_item_form(data_type, schema, form, item=None)
        elif db.find_one({"name": form_data["name"]}):
            flash("Name must be unique!")
            return _render_item_form(data_type, schema, form, item=None)

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

    dropdown_options = _build_dropdown_options(schema)
    form = form_generator.get_form(data_type, schema, formdata=request.form)
    form.populate_linked_fields(schema, dropdown_options)

    if not form.validate():
        flash("Form validation failed!")
        return _render_item_form(data_type, schema, form, item=None)

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
        return _render_item_form(data_type, schema, form, item=None)

    if "name" in form_data:
        if data_type == "units":
            if db.find_one({"name": form_data["name"], "era": form_data.get("era")}):
                flash("A unit with this name already exists in this era!")
                return _render_item_form(data_type, schema, form, item=None)
        elif db.find_one({"name": form_data["name"]}):
            flash("Name must be unique!")
            return _render_item_form(data_type, schema, form, item=None)

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
    if data_type in _ADMIN_ONLY_EDIT_TYPES and not (g.user and g.user.get("is_admin", False)):
        flash("You must be an admin to access this page.")
        return redirect(url_for("base_routes.home"))

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

    form = form_generator.get_form(data_type, schema, item=item)
    return _render_item_form(data_type, schema, form, item=item, item_ref=item_ref)

@data_item_routes.route("/<data_type>/edit/<item_ref>/request", methods=["POST"])
def data_item_edit_request(data_type, item_ref):
    if data_type in _ADMIN_ONLY_EDIT_TYPES and not (g.user and g.user.get("is_admin", False)):
        flash("You must be an admin to access this page.")
        return redirect(url_for("base_routes.home"))

    schema, db, item = get_data_on_item(data_type, item_ref)

    if data_type == "nations": #This should never happen, but just a good fallback
        print("Had to redirect from data_item_routes to nation_routes")
        return nation_edit_request(item_ref)
    
    form = form_generator.get_form(data_type, schema, formdata=request.form)
    dropdown_options = _build_dropdown_options(schema)
    form.populate_linked_fields(schema, dropdown_options)

    if not form.validate():
        flash("Form validation failed!")
        flash(form.errors)
        return _render_item_form(data_type, schema, form, item=item, item_ref=item_ref)

    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    if "name" in form_data:
        form_data["name"] = form_data.get("name", "").strip()

    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
        return _render_item_form(data_type, schema, form, item=item, item_ref=item_ref)

    if "name" in form_data:
        if data_type == "units":
            if db.find_one({"name": form_data["name"], "era": form_data.get("era"), "_id": {"$ne": item["_id"]}}):
                flash("A unit with this name already exists in this era!")
                return _render_item_form(data_type, schema, form, item=item, item_ref=item_ref)
        elif form_data["name"] != item.get("name") and db.find_one({"name": form_data["name"]}):
            flash("Name must be unique!")
            return _render_item_form(data_type, schema, form, item=item, item_ref=item_ref)

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
    dropdown_options = _build_dropdown_options(schema)
    form.populate_linked_fields(schema, dropdown_options)

    if not form.validate():
        flash("Form validation failed!")
        flash(form.errors)
        return _render_item_form(data_type, schema, form, item=item, item_ref=item_ref)

    form_data = form.data.copy()
    form_data.pop('csrf_token', None)
    form_data.pop('submit', None)
    if "name" in form_data:
        form_data["name"] = form_data.get("name", "").strip()

    valid, error = validate_form_with_jsonschema(form, schema)
    if not valid:
        flash(f"Validation Error: {error}")
        return _render_item_form(data_type, schema, form, item=item, item_ref=item_ref)

    if "name" in form_data:
        if data_type == "units":
            if db.find_one({"name": form_data["name"], "era": form_data.get("era"), "_id": {"$ne": item["_id"]}}):
                flash("A unit with this name already exists in this era!")
                return _render_item_form(data_type, schema, form, item=item, item_ref=item_ref)
        elif form_data["name"] != item.get("name") and db.find_one({"name": form_data["name"]}):
            flash("Name must be unique!")
            return _render_item_form(data_type, schema, form, item=item, item_ref=item_ref)

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

    # Archived wonders still exist and stay visible in the list, but are
    # excluded from the running total cost — they aren't owned by any
    # nation, so they shouldn't count against the pool everyone draws from.
    active_wonder_count = sum(1 for item in items if not item.get("archived"))

    return render_template(
        "wonder_list.html",
        title=category_data["wonders"]["pluralName"],
        items=items,
        schema=schema,
        preview_references=preview_overall_lookup_dict,
        active_wonder_count=active_wonder_count
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

def _apply_trade_fulfillment_to_nation(after_data, trade_type, resource, price, quantity):
    """Apply both sides of a fulfilled trade to the AI nation.

    A player fulfilling a "Sell" desire is buying the resource from the AI —
    the AI gains money and loses the resource (limited to what it holds).
    A player fulfilling a "Buy"/"Need to Buy" desire is selling the resource
    to the AI — the AI gains the resource and pays for it (limited to what it
    can afford). Mutates after_data in place, clamped to the nation's
    money/resource caps. Returns the quantity actually fulfilled.
    """
    storage = dict(after_data.get("resource_storage", {}))
    if "Sell" in trade_type:
        available = storage.get(resource, 0)
        quantity = max(0, min(quantity, available))
        if quantity <= 0:
            return 0
        storage[resource] = available - quantity
        after_data["resource_storage"] = storage
        money_cap = after_data.get("money_capacity")
        new_money = after_data.get("money", 0) + (price * quantity)
        if money_cap is not None:
            new_money = min(new_money, money_cap)
        after_data["money"] = new_money
    elif "Buy" in trade_type:
        money = after_data.get("money", 0)
        if price > 0:
            quantity = max(0, min(quantity, int(money // price)))
        if quantity <= 0:
            return 0
        new_amount = storage.get(resource, 0) + quantity
        resource_cap = after_data.get("nation_resource_capacity", {}).get(resource)
        if resource_cap is not None:
            new_amount = min(new_amount, resource_cap)
        storage[resource] = new_amount
        after_data["resource_storage"] = storage
        after_data["money"] = money - (price * quantity)
    return quantity


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
    fulfilled_quantity = None
    for i, desire in enumerate(after_data.get("resource_desires", [])):
        if desire.get("resource") == resource and desire.get("trade_type") == trade_type and desire.get("price") == price:
            current_quantity = desire.get("quantity", 0)
            requested = min(quantity, current_quantity)
            fulfilled_quantity = (
                _apply_trade_fulfillment_to_nation(after_data, trade_type, resource, price, requested)
                if requested > 0 else 0
            )
            after_data["resource_desires"][i]["quantity"] = current_quantity - fulfilled_quantity
            break

    if fulfilled_quantity == 0:
        flash("Trade could not be fulfilled — the nation lacks the stock or funds to complete it.")
        return redirect(request.referrer or "/markets")

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
    fulfilled_quantity = None
    for i, desire in enumerate(after_data.get("resource_desires", [])):
        if desire.get("resource") == resource and desire.get("trade_type") == trade_type and desire.get("price") == price:
            current_quantity = desire.get("quantity", 0)
            requested = min(quantity, current_quantity)
            fulfilled_quantity = (
                _apply_trade_fulfillment_to_nation(after_data, trade_type, resource, price, requested)
                if requested > 0 else 0
            )
            after_data["resource_desires"][i]["quantity"] = current_quantity - fulfilled_quantity
            break

    if fulfilled_quantity == 0:
        flash("Trade could not be fulfilled — the nation lacks the stock or funds to complete it.")
        return redirect(request.referrer or "/markets")

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
