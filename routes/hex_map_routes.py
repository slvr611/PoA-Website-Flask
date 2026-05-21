import os
import math
from flask import Blueprint, request, jsonify, render_template, g
from bson import ObjectId
from app_core import mongo, json_data, upload_bytes_to_s3
from helpers.auth_helpers import admin_required
from helpers.hex_map_helpers import (
    get_all_tiles,
    get_neighbor_tiles,
    is_tile_legally_controllable,
    get_nation_tile_stats,
    snapshot_current_map,
    name_to_color,
    get_nation_node_proximity_restrictions,
    get_node_resource_positions,
    is_within_distance_of_node_resource,
)

hex_map_routes = Blueprint("hex_map_routes", __name__)


def _name_to_color(name):
    return name_to_color(name)


def _get_nation_colors():
    nations = list(mongo.db.nations.find({}, {"name": 1, "accent_color": 1, "_id": 0}))
    return {
        n["name"]: n.get("accent_color") or _name_to_color(n["name"])
        for n in nations
        if n.get("name")
    }


def _get_nation_list():
    """Returns [{name, color, overlord, admin, nomadic}] for all nations — used for autocomplete, color lookup, map labels, and admin range display."""
    nations = list(mongo.db.nations.find({}, {"name": 1, "accent_color": 1, "overlord": 1, "administration": 1, "is_nomadic": 1, "_id": 1}))

    overlord_ids = set()
    for n in nations:
        ov = n.get("overlord")
        if ov:
            try:
                overlord_ids.add(ObjectId(str(ov)))
            except Exception:
                pass

    overlord_names = {}
    if overlord_ids:
        for on in mongo.db.nations.find({"_id": {"$in": list(overlord_ids)}}, {"name": 1}):
            overlord_names[str(on["_id"])] = on.get("name", "")

    return [
        {
            "name": n["name"],
            "color": n.get("accent_color") or _name_to_color(n["name"]),
            "overlord": overlord_names.get(str(n.get("overlord") or ""), ""),
            "admin": n.get("administration", 1),
            "nomadic": bool(n.get("is_nomadic", False)),
        }
        for n in nations
        if n.get("name")
    ]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _resource_color_map():
    """Build {key: color} for all resources, reading the optional color field."""
    result = {}
    for category in ("general_resources", "unique_resources", "luxury_resources"):
        for r in json_data.get(category, []):
            if r.get("color"):
                result[r["key"]] = r["color"]
    return result


@hex_map_routes.route("/api/hex-map/config")
def hex_map_config():
    cfg = mongo.db.global_modifiers.find_one({"name": "hex_map_config"}) or {}
    return jsonify(
        {
            "cols":         cfg.get("cols",         20),
            "rows":         cfg.get("rows",         15),
            "hex_size":     cfg.get("hex_size",     40),
            "bg_offset_x":  cfg.get("bg_offset_x",  0),
            "bg_offset_y":  cfg.get("bg_offset_y",  0),
            "bg_scale":     cfg.get("bg_scale",     1.0),
            "resource_colors": _resource_color_map(),
        }
    )


@hex_map_routes.route("/api/hex-map/config", methods=["POST"])
def update_hex_map_config():
    if getattr(g, "edit_access_level", 0) < 10:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json() or {}
    allowed = {"cols", "rows", "hex_size", "bg_offset_x", "bg_offset_y", "bg_scale"}
    update = {k: v for k, v in data.items() if k in allowed}
    if update:
        mongo.db.global_modifiers.update_one(
            {"name": "hex_map_config"}, {"$set": update}, upsert=True
        )
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------

def _cfg_fields(doc):
    """Extract cols/rows/hex_size from a config or snapshot document."""
    out = {}
    for key, default in (("cols", 20), ("rows", 15), ("hex_size", 40)):
        val = doc.get(key)
        if val:
            out[key] = val
    return out


@hex_map_routes.route("/api/hex-map/nation-list")
def hex_map_nation_list():
    return jsonify({"nations": _get_nation_list()})


@hex_map_routes.route("/api/hex-map/nation/<path:nation_name>/buildings")
def nation_buildings(nation_name):
    """Returns the cities and districts belonging to a nation, for tile placement."""
    nation = mongo.db.nations.find_one(
        {"name": nation_name},
        {"cities": 1, "districts": 1, "imperial_district": 1, "_id": 0},
    )
    if not nation:
        return jsonify({"cities": [], "districts": []})

    cities = [
        {"id": c["_id"], "name": c.get("name", ""), "type": c.get("type", "")}
        for c in nation.get("cities", [])
    ]

    # Collect all def_keys that need display name lookups
    def_keys = [d["def_key"] for d in nation.get("districts", []) if d.get("def_key")]
    imp_data = nation.get("imperial_district")
    if imp_data and imp_data.get("def_key"):
        def_keys.append(imp_data["def_key"])

    def_display_names = {}
    if def_keys:
        for doc in mongo.db.district_defs.find({"key": {"$in": def_keys}}, {"key": 1, "display_name": 1, "_id": 0}):
            def_display_names[doc["key"]] = doc.get("display_name", doc["key"])

    def _district_label(d):
        if d.get("def_key"):
            return def_display_names.get(d["def_key"], d["def_key"])
        return d.get("type", "")

    districts = []
    if imp_data:
        label = _district_label(imp_data) or imp_data.get("type", "")
        entry = {"id": imp_data.get("_id", "imperial"), "type": imp_data.get("type", ""),
                 "display_name": label, "imperial": True}
        if imp_data.get("def_key"):
            entry["def_key"] = imp_data["def_key"]
        districts.append(entry)

    for d in nation.get("districts", []):
        entry = {"id": d["_id"], "type": d.get("type", ""), "display_name": _district_label(d)}
        if d.get("def_key"):
            entry["def_key"] = d["def_key"]
        districts.append(entry)

    return jsonify({"cities": cities, "districts": districts})


@hex_map_routes.route("/api/hex-map/wonder-list")
def wonder_list_for_map():
    """Returns all wonders for tile placement."""
    wonders = list(mongo.db.wonders.find({}, {"name": 1, "owner_nation": 1, "_id": 1}))

    # Bulk-resolve owner nation names from string _id values
    owner_ids = set()
    for w in wonders:
        val = w.get("owner_nation")
        if val and isinstance(val, str):
            try:
                owner_ids.add(ObjectId(val))
            except Exception:
                pass
    nation_names = {}
    if owner_ids:
        for n in mongo.db.nations.find({"_id": {"$in": list(owner_ids)}}, {"name": 1}):
            nation_names[str(n["_id"])] = n.get("name", "")

    result = []
    for w in wonders:
        owner_raw = w.get("owner_nation") or ""
        if isinstance(owner_raw, dict):
            owner_name = owner_raw.get("name", "")   # legacy dict format
        else:
            owner_name = nation_names.get(str(owner_raw), "")
        result.append({
            "id":           str(w["_id"]),
            "name":         w.get("name", ""),
            "owner_nation": owner_name,
        })
    return jsonify(result)


@hex_map_routes.route("/api/hex-map/debug-colors")
def hex_map_debug_colors():
    """Debug endpoint: shows the raw accent_color field and computed map color for every nation."""
    nations = list(mongo.db.nations.find({}, {"name": 1, "accent_color": 1, "_id": 0}))
    rows = []
    for n in nations:
        if not n.get("name"):
            continue
        stored = n.get("accent_color")
        rows.append({
            "name":        n["name"],
            "accent_color_raw": stored,
            "map_color":   stored or _name_to_color(n["name"]),
            "source":      "accent_color" if stored else "generated",
        })
    # Also include any tile owners not in the nation list
    tile_owners = mongo.db.hex_map_tiles.distinct("owner")
    nation_names = {r["name"] for r in rows}
    for owner in tile_owners:
        if owner and owner not in nation_names:
            rows.append({
                "name":           owner,
                "accent_color_raw": None,
                "map_color":      _name_to_color(owner),
                "source":         "generated (nation not found)",
            })
    return jsonify({"nations": rows})


@hex_map_routes.route("/api/hex-map/tiles")
def hex_map_tiles():
    cfg = mongo.db.global_modifiers.find_one({"name": "hex_map_config"}) or {}
    resp = {"tiles": get_all_tiles(), "nation_colors": _get_nation_colors()}
    resp.update(_cfg_fields(cfg))
    return jsonify(resp)


@hex_map_routes.route("/api/hex-map/tiles/<int:session_num>")
def hex_map_tiles_session(session_num):
    snapshot = mongo.db.hex_map_history.find_one({"session": session_num})
    if snapshot:
        tiles  = snapshot.get("tiles", [])
        src    = snapshot
        # Use colors saved at snapshot time; fall back to live colors for any missing nation
        saved  = snapshot.get("nation_colors", {})
        live   = _get_nation_colors()
        colors = {**live, **saved}   # saved takes precedence
    else:
        tiles  = get_all_tiles()
        src    = mongo.db.global_modifiers.find_one({"name": "hex_map_config"}) or {}
        colors = _get_nation_colors()
    resp = {"tiles": tiles, "nation_colors": colors}
    resp.update(_cfg_fields(src))
    return jsonify(resp)


def _node_resource(node):
    """Extract the resource type string from a tile node dict or string."""
    if not node:
        return ""
    if isinstance(node, dict):
        return node.get("resource_type") or node.get("value") or ""
    return str(node)


def _sync_district_node(district_ref, node_resource, owner_name):
    """Write the tile's node resource type into the placed district instance."""
    if not isinstance(district_ref, dict) or not district_ref.get("id"):
        return
    instance_id = str(district_ref["id"])
    if not owner_name:
        return
    nation = mongo.db.nations.find_one(
        {"name": owner_name}, {"districts": 1, "imperial_district": 1}
    )
    if not nation:
        return
    for d in nation.get("districts", []):
        if isinstance(d, dict) and str(d.get("_id", "")) == instance_id:
            mongo.db.nations.update_one(
                {"name": owner_name, "districts._id": instance_id},
                {"$set": {"districts.$.node": node_resource}},
            )
            return
    imp = nation.get("imperial_district") or {}
    if isinstance(imp, dict) and str(imp.get("_id", "")) == instance_id:
        mongo.db.nations.update_one(
            {"name": owner_name},
            {"$set": {"imperial_district.node": node_resource}},
        )


def _clear_district_node(district_ref, owner_name):
    """Clear the node field on a district instance when it's removed from a tile."""
    _sync_district_node(district_ref, "", owner_name)


def _grid_bounds():
    """Return (max_cols, max_rows) from the stored hex map config."""
    cfg = mongo.db.global_modifiers.find_one({"name": "hex_map_config"}) or {}
    return cfg.get("cols", 20), cfg.get("rows", 15)


def _tile_in_bounds(q, r, max_cols, max_rows):
    """Return True if (q, r) falls within the configured flat-top even-q grid."""
    col = q
    row = r + math.floor(q / 2)
    return 0 <= col < max_cols and 0 <= row < max_rows


def purge_out_of_bounds_tiles():
    """Delete tiles whose flat-top offset falls outside the configured grid.

    This removes phantom tiles left behind when the coordinate system changed
    from pointy-top to flat-top — they're invisible in the viewer but still
    pollute owner tile counts and territory_types.

    Returns the number of tiles deleted.
    """
    max_cols, max_rows = _grid_bounds()
    all_tiles = list(mongo.db.hex_map_tiles.find({}, {"q": 1, "r": 1}))
    stale_ids = [
        t["_id"] for t in all_tiles
        if not _tile_in_bounds(t["q"], t["r"], max_cols, max_rows)
    ]
    if stale_ids:
        mongo.db.hex_map_tiles.delete_many({"_id": {"$in": stale_ids}})
    return len(stale_ids)


def _resync_nation_territory(nation_name):
    """Recount terrain types from hex_map_tiles and write to nations.territory_types."""
    if not nation_name:
        return
    pipeline = [
        {"$match": {"owner": nation_name, "terrain": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": "$terrain", "count": {"$sum": 1}}},
    ]
    counts = {doc["_id"]: doc["count"] for doc in mongo.db.hex_map_tiles.aggregate(pipeline)}
    mongo.db.nations.update_one({"name": nation_name}, {"$set": {"territory_types": counts}})


def _sync_wonder(wonder_ref, owner_name, node_resource):
    """Update a wonder's owner_nation (string _id) and node from tile data."""
    if not isinstance(wonder_ref, dict) or not wonder_ref.get("id"):
        return
    try:
        wid = ObjectId(str(wonder_ref["id"]))
    except Exception:
        return
    update = {"node": node_resource}
    if owner_name:
        nation = mongo.db.nations.find_one({"name": owner_name}, {"_id": 1})
        update["owner_nation"] = str(nation["_id"]) if nation else None
    else:
        update["owner_nation"] = None
    mongo.db.wonders.update_one({"_id": wid}, {"$set": update})


@hex_map_routes.route("/api/hex-map/tile/<signed_int:q>/<signed_int:r>", methods=["POST"])
def update_hex_map_tile(q, r):
    if getattr(g, "edit_access_level", 0) < 10:
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json() or {}
    allowed = {"terrain", "node", "city", "district", "wonder", "owner", "capital", "region"}
    update = {k: v for k, v in data.items() if k in allowed}
    if not update:
        return jsonify({"ok": True})

    # Enforce territory restrictions before saving
    if "owner" in update and update["owner"]:
        if not is_tile_legally_controllable(q, r, update["owner"]):
            return jsonify({"error": "This tile cannot be claimed: it violates a territory restriction."}), 422

    # Find the existing tile using a type-flexible query (q/r may be stored as int or float)
    # to avoid a silent upsert-creates-duplicate bug when types don't match exactly.
    current = mongo.db.hex_map_tiles.find_one(
        {"q": {"$in": [q, float(q)]}, "r": {"$in": [r, float(r)]}}
    ) or {}

    update["q"] = q
    update["r"] = r
    if current.get("_id"):
        mongo.db.hex_map_tiles.update_one({"_id": current["_id"]}, {"$set": update})
        # Remove any duplicate documents at the same coordinate so stale copies
        # don't win on the next page load.
        mongo.db.hex_map_tiles.delete_many({
            "q": {"$in": [q, float(q)]},
            "r": {"$in": [r, float(r)]},
            "_id": {"$ne": current["_id"]},
        })
    else:
        mongo.db.hex_map_tiles.insert_one(update)

    # Effective state after update
    effective = {**current, **update}
    eff_node     = effective.get("node") or {}
    eff_owner    = effective.get("owner", "")
    eff_district = effective.get("district") or {}
    eff_wonder   = effective.get("wonder") or {}
    node_res     = _node_resource(eff_node)

    prev_district = current.get("district") or {}
    prev_wonder   = current.get("wonder") or {}
    prev_owner    = current.get("owner", "")

    # ── District node sync ──────────────────────────────────────────────────
    if "district" in update:
        new_district = update.get("district") or {}
        if prev_district.get("id") and not new_district.get("id"):
            # District removed — clear its node
            _clear_district_node(prev_district, prev_owner)
        elif new_district.get("id"):
            # District placed (or replaced) — write current tile node
            _sync_district_node(new_district, node_res, eff_owner)
    elif ("node" in update or "owner" in update) and eff_district.get("id"):
        # Node or owner changed on a tile that already has a district
        _sync_district_node(eff_district, node_res, eff_owner)

    # ── Wonder owner / node sync ─────────────────────────────────────────────
    if "wonder" in update:
        new_wonder = update.get("wonder") or {}
        if prev_wonder.get("id") and not new_wonder.get("id"):
            # Wonder removed — clear its owner
            _sync_wonder(prev_wonder, "", "")
        elif new_wonder.get("id"):
            # Wonder placed (or replaced)
            _sync_wonder(new_wonder, eff_owner, node_res)
    elif ("owner" in update or "node" in update) and eff_wonder.get("id"):
        # Owner or node changed on a tile that already has a wonder
        _sync_wonder(eff_wonder, eff_owner, node_res)

    # ── Territory type sync ───────────────────────────────────────────────────
    if "owner" in update or "terrain" in update:
        _resync_nation_territory(eff_owner)
        if "owner" in update and prev_owner and prev_owner != eff_owner:
            _resync_nation_territory(prev_owner)

    return jsonify({"ok": True})


@hex_map_routes.route("/api/hex-map/purge-oob-tiles", methods=["POST"])
@admin_required
def purge_oob_tiles():
    """Delete tiles outside the current configured grid bounds.

    Run this after configuring the correct cols/rows to remove phantom tiles
    left behind by coordinate system changes (e.g., pointy-top → flat-top).
    """
    deleted = purge_out_of_bounds_tiles()
    # Re-sync territory for all nations after the purge
    pipeline = [
        {"$match": {"owner": {"$exists": True, "$ne": None, "$ne": ""}, "terrain": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": {"owner": "$owner", "terrain": "$terrain"}, "count": {"$sum": 1}}},
    ]
    nation_counts = {}
    for doc in mongo.db.hex_map_tiles.aggregate(pipeline):
        nation_counts.setdefault(doc["_id"]["owner"], {})[doc["_id"]["terrain"]] = doc["count"]
    for nation_name, counts in nation_counts.items():
        mongo.db.nations.update_one({"name": nation_name}, {"$set": {"territory_types": counts}})
    owned = set(nation_counts.keys())
    mongo.db.nations.update_many(
        {"name": {"$nin": list(owned)}, "territory_types": {"$exists": True}},
        {"$set": {"territory_types": {}}},
    )
    return jsonify({"ok": True, "tiles_deleted": deleted, "nations_resynced": len(nation_counts)})


@hex_map_routes.route("/api/hex-map/normalize-tile-coords", methods=["POST"])
@admin_required
def normalize_tile_coords():
    """Fix tile coordinate types and remove duplicate tiles at the same position.

    Two problems are fixed in one pass:
    1. Float q/r (e.g. 5.0) converted to integer (5).
    2. Duplicate documents at the same (q, r) — the one with the most fields
       (most data) is kept; all others are deleted.

    Safe to run multiple times.
    """
    from collections import defaultdict
    all_tiles = list(mongo.db.hex_map_tiles.find({}, {"_id": 1, "q": 1, "r": 1}))

    # Step 1: normalise float coords to int
    type_fixed = 0
    for tile in all_tiles:
        q_val, r_val = tile.get("q"), tile.get("r")
        if not isinstance(q_val, int) or not isinstance(r_val, int):
            try:
                mongo.db.hex_map_tiles.update_one(
                    {"_id": tile["_id"]},
                    {"$set": {"q": int(q_val), "r": int(r_val)}},
                )
                tile["q"] = int(q_val)
                tile["r"] = int(r_val)
                type_fixed += 1
            except (TypeError, ValueError):
                pass

    # Step 2: find and remove duplicates — group by (q, r), keep richest doc
    coord_groups = defaultdict(list)
    for tile in all_tiles:
        try:
            coord_groups[(int(tile["q"]), int(tile["r"]))].append(tile["_id"])
        except (TypeError, ValueError, KeyError):
            pass

    dupes_removed = 0
    for (q_int, r_int), ids in coord_groups.items():
        if len(ids) <= 1:
            continue
        # Fetch full docs to pick the one with the most fields (most complete data)
        docs = list(mongo.db.hex_map_tiles.find({"_id": {"$in": ids}}))
        best = max(docs, key=lambda d: len(d))
        stale_ids = [d["_id"] for d in docs if d["_id"] != best["_id"]]
        mongo.db.hex_map_tiles.delete_many({"_id": {"$in": stale_ids}})
        dupes_removed += len(stale_ids)

    return jsonify({"ok": True, "types_fixed": type_fixed, "duplicates_removed": dupes_removed})


@hex_map_routes.route("/api/hex-map/sync-all-territory", methods=["POST"])
@admin_required
def sync_all_territory():
    """Recount terrain types for every nation from current tile data.

    Also purges any tiles outside the configured grid bounds (stale tiles
    from old coordinate systems are invisible and must not count).
    """
    purge_out_of_bounds_tiles()

    pipeline = [
        {"$match": {"owner": {"$exists": True, "$ne": None, "$ne": ""}, "terrain": {"$exists": True, "$ne": None}}},
        {"$group": {"_id": {"owner": "$owner", "terrain": "$terrain"}, "count": {"$sum": 1}}},
    ]
    nation_counts = {}
    for doc in mongo.db.hex_map_tiles.aggregate(pipeline):
        owner   = doc["_id"]["owner"]
        terrain = doc["_id"]["terrain"]
        nation_counts.setdefault(owner, {})[terrain] = doc["count"]

    updated = 0
    for nation_name, counts in nation_counts.items():
        mongo.db.nations.update_one({"name": nation_name}, {"$set": {"territory_types": counts}})
        updated += 1

    # Zero out nations that own no tiles
    owned_nations = set(nation_counts.keys())
    mongo.db.nations.update_many(
        {"name": {"$nin": list(owned_nations)}, "territory_types": {"$exists": True}},
        {"$set": {"territory_types": {}}},
    )
    return jsonify({"ok": True, "nations_updated": updated})


@hex_map_routes.route("/api/hex-map/tile/<signed_int:q>/<signed_int:r>/adjacency")
def hex_tile_adjacency(q, r):
    return jsonify({"neighbors": get_neighbor_tiles(q, r)})


# ---------------------------------------------------------------------------
# City type images
# ---------------------------------------------------------------------------

_ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}


@hex_map_routes.route("/api/cities/image-map")
def city_type_image_map():
    """Returns {city_type_key: image_url} for all types that have a custom image."""
    doc = mongo.db.global_modifiers.find_one({"name": "city_type_images"}) or {}
    return jsonify(doc.get("images", {}))


@hex_map_routes.route("/admin/grid-calibration")
@admin_required
def grid_calibration_page():
    return render_template("grid_calibration.html")


@hex_map_routes.route("/admin/city-type-images")
@admin_required
def city_type_images_page():
    doc = mongo.db.global_modifiers.find_one({"name": "city_type_images"}) or {}
    current_images = doc.get("images", {})
    city_types = [
        {"key": k, "display_name": v.get("display_name", k), "image": current_images.get(k, "")}
        for k, v in json_data.get("cities", {}).items()
    ]
    return render_template("city_type_images.html", city_types=city_types)


@hex_map_routes.route("/admin/city-type-images/upload", methods=["POST"])
@admin_required
def city_type_image_upload():
    if "image" not in request.files:
        return jsonify({"success": False, "error": "No file provided"}), 400
    file = request.files["image"]
    if not file.filename:
        return jsonify({"success": False, "error": "No file selected"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in _ALLOWED_IMAGE_EXTENSIONS:
        return jsonify({"success": False, "error": f"File type '{ext}' not allowed"}), 400

    city_type = request.form.get("city_type", "").strip()
    if not city_type:
        return jsonify({"success": False, "error": "No city_type provided"}), 400

    safe_type = "".join(c if c.isalnum() or c in "-_" else "_" for c in city_type).lower()
    s3_key = f"city_type_images/{safe_type}{ext}"

    file_bytes = file.read()
    content_type = file.content_type or "image/jpeg"

    success, result = upload_bytes_to_s3(file_bytes, s3_key, content_type)
    if success:
        mongo.db.global_modifiers.update_one(
            {"name": "city_type_images"},
            {"$set": {f"images.{city_type}": result}},
            upsert=True,
        )
        return jsonify({"success": True, "url": result})
    else:
        return jsonify({"success": False, "error": result}), 500


# ---------------------------------------------------------------------------
# Nation stats / reachability
# ---------------------------------------------------------------------------

@hex_map_routes.route("/api/hex-map/nation/<path:nation_name>/stats")
def nation_tile_stats(nation_name):
    return jsonify(get_nation_tile_stats(nation_name))


@hex_map_routes.route("/api/hex-map/nation/<path:nation_name>/reachable/<signed_int:q>/<signed_int:r>")
def nation_tile_reachable(nation_name, q, r):
    return jsonify({"reachable": is_tile_legally_controllable(q, r, nation_name)})


@hex_map_routes.route("/api/hex-map/nation/<path:nation_name>/restriction-tiles")
def nation_restriction_tiles(nation_name):
    """Return all map tiles that violate the nation's territory proximity restrictions."""
    restrictions = get_nation_node_proximity_restrictions(nation_name)
    if not restrictions:
        return jsonify({"forbidden": []})
    all_tiles = list(mongo.db.hex_map_tiles.find({}, {"q": 1, "r": 1, "_id": 0}))
    forbidden = set()
    for resource_type, max_distance in restrictions:
        node_positions = get_node_resource_positions(resource_type)
        if not node_positions:
            continue
        for tile in all_tiles:
            q, r = tile["q"], tile["r"]
            if not is_within_distance_of_node_resource(q, r, resource_type, max_distance, node_positions):
                forbidden.add((q, r))
    return jsonify({"forbidden": [[q, r] for q, r in forbidden]})


# ---------------------------------------------------------------------------
# Region endpoints
# ---------------------------------------------------------------------------

@hex_map_routes.route("/api/hex-map/region-list")
def hex_region_list():
    """Return all regions with their map colors, pulled from the regions collection."""
    regions = list(mongo.db.regions.find({}, {"name": 1, "map_color": 1, "_id": 0}))
    return jsonify({"regions": [
        {"name": r["name"], "color": r.get("map_color") or "#888888"}
        for r in regions if r.get("name")
    ]})


@hex_map_routes.route("/api/hex-map/region/<path:region_name>/color", methods=["POST"])
@admin_required
def update_region_map_color(region_name):
    """Update just the map_color field on an existing region."""
    data  = request.get_json() or {}
    color = (data.get("color") or "#888888").strip()
    result = mongo.db.regions.update_one({"name": region_name}, {"$set": {"map_color": color}})
    if result.matched_count == 0:
        return jsonify({"error": "Region not found"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Sync nation regions
# ---------------------------------------------------------------------------

@hex_map_routes.route("/api/hex-map/sync-nation-regions", methods=["POST"])
@admin_required
def sync_nation_regions():
    """Set each nation's region field based on their capital tile's region.

    Falls back to the region covering the majority of the nation's tiles if
    no capital tile exists or the capital has no region assigned.
    """
    region_id_map = {
        r["name"]: str(r["_id"])
        for r in mongo.db.regions.find({}, {"name": 1})
        if r.get("name")
    }

    updated = 0
    skipped = 0

    for nation in mongo.db.nations.find({}, {"name": 1}):
        name = nation.get("name")
        if not name:
            continue

        region_id = None

        # Prefer capital tile's region
        capital_tile = mongo.db.hex_map_tiles.find_one(
            {"owner": name, "capital": True},
            {"region": 1}
        )
        if capital_tile and capital_tile.get("region"):
            region_id = region_id_map.get(capital_tile["region"])

        # Fallback: region with the most owned tiles
        if not region_id:
            pipeline = [
                {"$match": {"owner": name, "region": {"$exists": True, "$ne": None, "$ne": ""}}},
                {"$group": {"_id": "$region", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 1},
            ]
            result = list(mongo.db.hex_map_tiles.aggregate(pipeline))
            if result:
                region_id = region_id_map.get(result[0]["_id"])

        if region_id:
            mongo.db.nations.update_one({"name": name}, {"$set": {"region": region_id}})
            updated += 1
        else:
            skipped += 1

    return jsonify({"ok": True, "updated": updated, "skipped": skipped})


# ---------------------------------------------------------------------------
# Manual snapshot endpoint
# ---------------------------------------------------------------------------

@hex_map_routes.route("/api/hex-map/snapshot", methods=["POST"])
def take_snapshot():
    if getattr(g, "edit_access_level", 0) < 10:
        return jsonify({"error": "Unauthorized"}), 403
    gm = mongo.db.global_modifiers.find_one({"name": "global_modifiers"})
    session_num = gm.get("session_counter", 1) if gm else 1
    message = snapshot_current_map(session_num)
    return jsonify({"ok": True, "message": message})
