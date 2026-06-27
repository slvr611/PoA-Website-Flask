import os
from flask import Blueprint, render_template, request, redirect, flash, jsonify, g
from app_core import mongo, json_data, upload_bytes_to_s3
from helpers.auth_helpers import admin_required
from calculations.field_calculations import check_district_requirements
from pymongo import ASCENDING

_ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}

district_def_routes = Blueprint("district_def_routes", __name__)

_RESOURCE_KEYS = ["money", "food", "wood", "stone", "mounts", "magic", "bronze", "iron"]
_UPKEEP_KEYS = _RESOURCE_KEYS + ["research"]
_ALL_EXTRA_KEYS = ["resource", "resource_from", "resource_to", "job", "attribute", "unit_category", "unit_stat", "tier", "tech_category", "terrain"]


def _all_categories():
    return list(mongo.db.district_categories.find({}, {"_id": 0}).sort("sort_order", ASCENDING))


def _all_defs():
    return list(mongo.db.district_defs.find({}).sort([("category", ASCENDING), ("tier", ASCENDING), ("display_name", ASCENDING)]))


def _build_district_def_form_extras(item=None):
    categories = _all_categories()
    modifier_types = json_data.get("modifier_types", {})
    sorted_modifier_types = sorted(modifier_types.items(), key=lambda x: x[1].get("name", x[0]))
    scaling_types = json_data.get("scaling_types", {})

    tech_options = sorted(
        [{"key": k, "display_name": v.get("display_name", k)} for k, v in json_data.get("tech", {}).items()],
        key=lambda x: x["display_name"]
    )

    all_resources = [
        {"key": r.get("key", ""), "name": r.get("name", "")}
        for category in ("general_resources", "unique_resources", "luxury_resources")
        for r in json_data.get(category, [])
        if r.get("key")
    ]

    def _names(col):
        return [d["name"] for d in mongo.db[col].find({}, {"_id": 0, "name": 1}).sort("name", ASCENDING)]

    existing_def_keys = [d["key"] for d in _all_defs() if not item or d["key"] != item.get("key")]

    return dict(
        categories=categories,
        sorted_modifier_types=sorted_modifier_types,
        modifier_types_dict=modifier_types,
        scaling_types=scaling_types,
        tech_options=tech_options,
        races_options=_names("races"),
        nations_options=_names("nations"),
        existing_def_keys=existing_def_keys,
        resource_keys=_RESOURCE_KEYS,
        upkeep_keys=_UPKEEP_KEYS,
        all_resources=all_resources,
        all_extra_keys=_ALL_EXTRA_KEYS,
    )


def _parse_cost(form, prefix="cost_", keys=None):
    result = {}
    for key in (keys or _RESOURCE_KEYS):
        raw = form.get(f"{prefix}{key}", "").strip()
        if raw:
            try:
                val = int(raw)
                if val:
                    result[key] = val
            except ValueError:
                pass
    return result


def _parse_indexed_modifiers(form, field_prefix):
    """Parse modifiers using {field_prefix}-{j}-{subfield} naming."""
    mods = []
    j = 0
    while f"{field_prefix}-{j}-modifier_type" in form:
        mod_type = form.get(f"{field_prefix}-{j}-modifier_type", "").strip()
        if mod_type:
            try:
                entry = {"modifier_type": mod_type, "value": float(form.get(f"{field_prefix}-{j}-value", "0") or "0")}
                for ek in _ALL_EXTRA_KEYS:
                    ev = form.get(f"{field_prefix}-{j}-{ek}", "").strip()
                    if ev:
                        entry[ek] = ev
                sc = form.get(f"{field_prefix}-{j}-scaling", "flat").strip()
                if sc and sc != "flat":
                    entry["scaling"] = sc
                sx = form.get(f"{field_prefix}-{j}-scaling_x", "").strip()
                if sx:
                    try:
                        entry["scaling_x"] = float(sx)
                    except ValueError:
                        pass
                se = form.get(f"{field_prefix}-{j}-scaling_extra", "").strip()
                if se:
                    entry["scaling_extra"] = se
                mx = form.get(f"{field_prefix}-{j}-max_value", "").strip()
                if mx:
                    try:
                        entry["max_value"] = float(mx)
                    except ValueError:
                        pass
                scope = form.get(f"{field_prefix}-{j}-scope", "").strip()
                if scope:
                    entry["scope"] = scope
                mods.append(entry)
            except (ValueError, TypeError):
                pass
        j += 1
    return mods


def _parse_indexed_requirements(form, field_prefix):
    """Parse requirements using {field_prefix}-{j}-type / value naming."""
    reqs = []
    j = 0
    while f"{field_prefix}-{j}-type" in form:
        t = form.get(f"{field_prefix}-{j}-type", "").strip()
        v = form.get(f"{field_prefix}-{j}-value", "").strip()
        if t and v:
            reqs.append({"type": t, "value": v})
        j += 1
    return reqs


def _parse_modifiers(form):
    return _parse_indexed_modifiers(form, "modifiers")


def _parse_requirements(form, prefix="req_"):
    reqs = []
    types = form.getlist(f"{prefix}type")
    values = form.getlist(f"{prefix}value")
    for t, v in zip(types, values):
        t = t.strip()
        v = v.strip()
        if t and v:
            reqs.append({"type": t, "value": v})
    return reqs


def _parse_synergies(form):
    synergies = []
    i = 0
    while f"synergies-{i}-requirement" in form:
        req = form.get(f"synergies-{i}-requirement", "").strip()
        node_active = form.get(f"synergies-{i}-node_active", "0") == "1"
        mods = _parse_indexed_modifiers(form, f"synergies-{i}-modifiers")
        if req or mods:
            synergies.append({
                "requirement": req.split(",") if "," in req else req,
                "modifiers": mods,
                "node_active": node_active,
            })
        i += 1
    return synergies


def _parse_upgrades(form):
    upgrades = []
    i = 0
    while f"upgrades-{i}-key" in form:
        key = form.get(f"upgrades-{i}-key", "").strip()
        name = form.get(f"upgrades-{i}-display_name", "").strip()
        if key:
            reqs = _parse_indexed_requirements(form, f"upgrades-{i}-requirements")
            mods = _parse_indexed_modifiers(form, f"upgrades-{i}-modifiers")
            upgrades.append({"key": key, "display_name": name, "requirements": reqs, "modifiers": mods})
        i += 1
    return upgrades


def _normalize_def_for_edit(item):
    """Normalize synergy/upgrade modifiers from legacy dict format to list format for template rendering."""
    for syn in item.get("synergies", []):
        mods = syn.get("modifiers", [])
        if isinstance(mods, dict):
            syn["modifiers"] = [{"modifier": mk, "value": mv} for mk, mv in mods.items()]
    for upg in item.get("upgrades", []):
        mods = upg.get("modifiers", [])
        if isinstance(mods, dict):
            upg["modifiers"] = [{"modifier": mk, "value": mv} for mk, mv in mods.items()]
    return item


def _form_to_def(form, existing_key=None, existing_image=None):
    key = (existing_key or form.get("key", "").strip()).lower().replace(" ", "_")
    image = form.get("image", "").strip() or existing_image or ""
    tile_req = form.get("tile_requirement", "land").strip()
    if tile_req not in ("land", "coastal", "water"):
        tile_req = "land"
    doc = {
        "key": key,
        "display_name": form.get("display_name", "").strip(),
        "category": form.get("category", "").strip(),
        "tier": int(form.get("tier", 1)),
        "tile_requirement": tile_req,
        "allow_multiple": form.get("allow_multiple") == "on",
        "free_placement": form.get("free_placement") == "on",
        "map_count": max(1, int(form.get("map_count", 1) or 1)),
        "description": form.get("description", "").strip(),
        "cost": _parse_cost(form, "cost_"),
        "upkeep": _parse_cost(form, "upkeep_", keys=_UPKEEP_KEYS),
        "requirements": _parse_requirements(form, "req_"),
        "modifiers": _parse_modifiers(form),
        "synergies": _parse_synergies(form),
        "upgrades": _parse_upgrades(form),
    }
    if image:
        doc["image"] = image
    return doc


# ---------------------------------------------------------------------------
# Category routes
# ---------------------------------------------------------------------------

@district_def_routes.route("/district-categories")
@admin_required
def district_categories_list():
    categories = _all_categories()
    defs = _all_defs()
    cat_counts = {}
    for d in defs:
        cat_counts[d.get("category", "")] = cat_counts.get(d.get("category", ""), 0) + 1
    return render_template("district_categories_list.html", categories=categories, cat_counts=cat_counts)


@district_def_routes.route("/district-categories/new")
@admin_required
def district_categories_new():
    return render_template("district_categories_edit.html", item=None, title="New District Category")


@district_def_routes.route("/district-categories/new/save", methods=["POST"])
@admin_required
def district_categories_new_save():
    form = request.form
    key = form.get("key", "").strip().lower().replace(" ", "_")
    if not key:
        flash("Key is required.", "error")
        return redirect("/district-categories/new")
    if mongo.db.district_categories.find_one({"key": key}):
        flash("A category with that key already exists.", "error")
        return redirect("/district-categories/new")
    doc = {
        "key": key,
        "display_name": form.get("display_name", "").strip(),
        "description": form.get("description", "").strip(),
        "sort_order": int(form.get("sort_order", 0) or 0),
    }
    mongo.db.district_categories.insert_one(doc)
    flash("Category created.", "success")
    return redirect("/district-categories")


@district_def_routes.route("/district-categories/<key>/edit")
@admin_required
def district_categories_edit(key):
    item = mongo.db.district_categories.find_one({"key": key}, {"_id": 0})
    if not item:
        flash("Category not found.", "error")
        return redirect("/district-categories")
    return render_template("district_categories_edit.html", item=item, title=f"Edit Category: {item['display_name']}")


@district_def_routes.route("/district-categories/<key>/edit/save", methods=["POST"])
@admin_required
def district_categories_edit_save(key):
    form = request.form
    update = {
        "display_name": form.get("display_name", "").strip(),
        "description": form.get("description", "").strip(),
        "sort_order": int(form.get("sort_order", 0) or 0),
    }
    mongo.db.district_categories.update_one({"key": key}, {"$set": update})
    flash("Category updated.", "success")
    return redirect("/district-categories")


@district_def_routes.route("/district-categories/<key>/delete", methods=["POST"])
@admin_required
def district_categories_delete(key):
    if mongo.db.district_defs.find_one({"category": key}):
        flash("Cannot delete category — district definitions reference it.", "error")
        return redirect("/district-categories")
    mongo.db.district_categories.delete_one({"key": key})
    flash("Category deleted.", "success")
    return redirect("/district-categories")


# ---------------------------------------------------------------------------
# Definition routes
# ---------------------------------------------------------------------------

@district_def_routes.route("/district-defs")
@admin_required
def district_defs_list():
    defs = _all_defs()
    categories = {c["key"]: c for c in _all_categories()}
    return render_template("district_defs.html", defs=defs, categories=categories)


@district_def_routes.route("/district-defs/new")
@admin_required
def district_defs_new():
    extras = _build_district_def_form_extras()
    pre_category = request.args.get("category", "")
    pre_tier = int(request.args.get("tier", 1))
    return render_template("district_defs_edit.html", item=None, title="New District Definition",
                           pre_category=pre_category, pre_tier=pre_tier, **extras)


@district_def_routes.route("/district-defs/new/save", methods=["POST"])
@admin_required
def district_defs_new_save():
    doc = _form_to_def(request.form)
    if not doc["key"]:
        flash("Key is required.", "error")
        return redirect("/district-defs/new")
    if mongo.db.district_defs.find_one({"key": doc["key"]}):
        flash("A definition with that key already exists.", "error")
        return redirect("/district-defs/new")
    mongo.db.district_defs.insert_one(doc)
    flash("District definition created.", "success")
    return redirect("/district-defs")


@district_def_routes.route("/district-defs/<key>/edit")
@admin_required
def district_defs_edit(key):
    item = mongo.db.district_defs.find_one({"key": key})
    if not item:
        flash("Definition not found.", "error")
        return redirect("/district-defs")
    item["_id"] = str(item["_id"])
    item = _normalize_def_for_edit(item)
    extras = _build_district_def_form_extras(item=item)
    return render_template("district_defs_edit.html", item=item, title=f"Edit: {item['display_name']}", **extras)


@district_def_routes.route("/district-defs/<key>/edit/save", methods=["POST"])
@admin_required
def district_defs_edit_save(key):
    existing = mongo.db.district_defs.find_one({"key": key}, {"image": 1}) or {}
    doc = _form_to_def(request.form, existing_key=key, existing_image=existing.get("image", ""))
    mongo.db.district_defs.update_one({"key": key}, {"$set": doc})
    flash("District definition updated.", "success")
    return redirect(f"/district-defs/{key}/edit")


@district_def_routes.route("/district-defs/<key>/delete", methods=["POST"])
@admin_required
def district_defs_delete(key):
    if mongo.db.nations.find_one({"districts.def_key": key}):
        flash("Cannot delete — one or more nations have this district built.", "error")
        return redirect("/district-defs")
    mongo.db.district_defs.delete_one({"key": key})
    flash("District definition deleted.", "success")
    return redirect("/district-defs")


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

@district_def_routes.route("/api/district-defs")
def api_district_defs():
    defs = _all_defs()
    categories = {c["key"]: c for c in _all_categories()}
    result = []
    for d in defs:
        d["_id"] = str(d["_id"])
        cat = categories.get(d.get("category", ""), {})
        d["category_display"] = cat.get("display_name", d.get("category", ""))
        result.append(d)
    return jsonify(result)


@district_def_routes.route("/api/district-defs/available/<path:nation_name>")
def api_district_defs_available(nation_name):
    nation = mongo.db.nations.find_one({"name": nation_name})
    if not nation:
        return jsonify([])
    defs = _all_defs()
    available = []
    for d in defs:
        d["_id"] = str(d["_id"])
        if check_district_requirements(nation, d):
            available.append(d)
    return jsonify(available)


@district_def_routes.route("/api/district-defs/image-map")
def api_district_defs_image_map():
    """Returns {def_key: image_url} for all defs that have an image."""
    defs = mongo.db.district_defs.find({"image": {"$exists": True, "$ne": ""}}, {"key": 1, "image": 1, "_id": 0})
    return jsonify({d["key"]: d["image"] for d in defs})


@district_def_routes.route("/api/district-defs/upload-image", methods=["POST"])
@admin_required
def district_def_upload_image():
    if "image" not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400
    file = request.files["image"]
    if not file.filename:
        return jsonify({"success": False, "error": "No file selected"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in _ALLOWED_IMAGE_EXTENSIONS:
        return jsonify({"success": False, "error": f"File type '{ext}' not allowed"}), 400

    def_key = request.form.get("def_key", "unknown").strip()
    safe_key = "".join(c if c.isalnum() or c in "-_" else "_" for c in def_key).lower()
    s3_key = f"district_images/{safe_key}{ext}"

    file_bytes = file.read()
    content_type = file.content_type or "image/jpeg"

    success, result = upload_bytes_to_s3(file_bytes, s3_key, content_type)
    if success:
        return jsonify({"success": True, "url": result})
    else:
        return jsonify({"success": False, "error": result}), 500
