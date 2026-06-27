from flask import Blueprint, render_template, request, jsonify, g
from app_core import mongo
from helpers.auth_helpers import admin_required
from helpers.change_helpers import request_change, approve_change, system_request_change, system_approve_change
from pymongo import ASCENDING
from bson import ObjectId
import random

pops_routes = Blueprint("pops_routes", __name__)


def _sorted_options(collection_name):
    return [
        {"id": str(item["_id"]), "name": item.get("name", "")}
        for item in mongo.db[collection_name].find({}, {"_id": 1, "name": 1}).sort("name", ASCENDING)
    ]


@pops_routes.route("/pops/bulk_options")
def pops_bulk_options():
    """Return all race/culture/religion/nation options for bulk-edit dropdowns."""
    return jsonify({
        "nations": _sorted_options("nations"),
        "races": _sorted_options("races"),
        "cultures": _sorted_options("cultures"),
        "religions": _sorted_options("religions"),
    })


@pops_routes.route("/pops/bulk_create", methods=["GET"])
@admin_required
def pops_bulk_create():
    nations = [
        {
            "id": str(n["_id"]),
            "name": n.get("name", ""),
            "race": str(n.get("primary_race", "")),
            "culture": str(n.get("primary_culture", "")),
            "religion": str(n.get("primary_religion", "")),
            "pop_count": n.get("pop_count", 0),
        }
        for n in mongo.db.nations.find(
            {}, {"_id": 1, "name": 1, "primary_race": 1, "primary_culture": 1, "primary_religion": 1, "pop_count": 1}
        ).sort("name", ASCENDING)
    ]
    races = _sorted_options("races")
    cultures = _sorted_options("cultures")
    religions = _sorted_options("religions")

    return render_template(
        "pops_bulk_create.html",
        nations=nations,
        races=races,
        cultures=cultures,
        religions=religions,
    )


@pops_routes.route("/pops/bulk_create", methods=["POST"])
@admin_required
def pops_bulk_create_submit():
    pops_data = request.get_json()
    if not isinstance(pops_data, list):
        return jsonify({"error": "Expected a list of pops"}), 400

    created = 0
    errors = []
    for pop_dict in pops_data:
        nation = (pop_dict.get("nation") or "").strip()
        if not nation:
            errors.append("A pop is missing a nation — skipped.")
            continue
        after_data = {
            "nation": nation,
            "race": (pop_dict.get("race") or "").strip(),
            "culture": (pop_dict.get("culture") or "").strip(),
            "religion": (pop_dict.get("religion") or "").strip(),
        }
        change_id = request_change(
            data_type="pops",
            item_id=None,
            change_type="Add",
            before_data={},
            after_data=after_data,
            reason="Bulk pop creation",
        )
        if change_id:
            approve_change(change_id)
            created += 1
        else:
            errors.append(f"Failed to stage pop for nation {nation}")

    return jsonify({"created": created, "errors": errors})


@pops_routes.route("/pops/bulk_edit", methods=["POST"])
def pops_bulk_edit():
    """Create one Update change per selected pop; approve immediately if admin."""
    if not g.user:
        return jsonify({"error": "Not logged in"}), 401

    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid payload"}), 400

    updates = data.get("updates", [])
    reason = data.get("reason") or "Bulk pop edit"
    is_admin = bool(g.user.get("is_admin"))

    change_ids = []
    errors = []

    for upd in updates:
        raw_id = upd.get("pop_id", "")
        try:
            oid = ObjectId(raw_id)
        except Exception:
            errors.append(f"Invalid pop id: {raw_id}")
            continue

        pop = mongo.db.pops.find_one(
            {"_id": oid}, {"nation": 1, "race": 1, "culture": 1, "religion": 1}
        )
        if not pop:
            errors.append(f"Pop not found: {raw_id}")
            continue

        before = {
            "nation": pop.get("nation", ""),
            "race": pop.get("race", ""),
            "culture": pop.get("culture", ""),
            "religion": pop.get("religion", ""),
        }
        after = dict(before)
        for field in ("nation", "race", "culture", "religion"):
            if upd.get(field):
                after[field] = upd[field]

        if before == after:
            continue

        change_id = request_change(
            data_type="pops",
            item_id=pop["_id"],
            change_type="Update",
            before_data=dict(before),
            after_data=dict(after),
            reason=reason,
        )
        if not change_id:
            errors.append(f"Failed to create change for pop {raw_id}")
            continue

        if is_admin:
            approve_change(change_id)

        change_ids.append(str(change_id))

    return jsonify({
        "changes_created": len(change_ids),
        "approved": is_admin,
        "errors": errors,
    })


@pops_routes.route("/pops/bulk_delete", methods=["POST"])
def pops_bulk_delete():
    """Create one Remove change per selected pop; approve immediately if admin."""
    if not g.user:
        return jsonify({"error": "Not logged in"}), 401
    if not g.user.get("is_admin"):
        return jsonify({"error": "Admin required"}), 403

    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid payload"}), 400

    pop_ids = data.get("pop_ids", [])
    reason = data.get("reason") or "Bulk pop deletion"

    change_ids = []
    errors = []

    for raw_id in pop_ids:
        try:
            oid = ObjectId(raw_id)
        except Exception:
            errors.append(f"Invalid pop id: {raw_id}")
            continue

        pop = mongo.db.pops.find_one({"_id": oid})
        if not pop:
            errors.append(f"Pop not found: {raw_id}")
            continue

        before = {k: v for k, v in pop.items() if k != "_id"}
        change_id = request_change(
            data_type="pops",
            item_id=pop["_id"],
            change_type="Remove",
            before_data=before,
            after_data={},
            reason=reason,
        )
        if not change_id:
            errors.append(f"Failed to create change for pop {raw_id}")
            continue

        approve_change(change_id)
        change_ids.append(str(change_id))

    return jsonify({
        "deleted": len(change_ids),
        "errors": errors,
    })


@pops_routes.route("/pops/flee_pop", methods=["POST"])
@admin_required
def pops_flee_pop():
    """Move a single pop to a random non-Closed nation in the same region."""
    data = request.get_json() or {}
    raw_id = data.get("pop_id", "")
    try:
        pop_oid = ObjectId(raw_id)
    except Exception:
        return jsonify({"error": "Invalid pop id"}), 400

    pop = mongo.db.pops.find_one({"_id": pop_oid})
    if not pop:
        return jsonify({"error": "Pop not found"}), 404

    nation_id = pop.get("nation", "")
    if not nation_id:
        return jsonify({"error": "Pop has no nation"}), 400

    try:
        nation = mongo.db.nations.find_one({"_id": ObjectId(nation_id)}, {"region": 1, "name": 1})
    except Exception:
        return jsonify({"error": "Could not look up nation"}), 500

    if not nation:
        return jsonify({"error": "Nation not found"}), 404

    region_id = str(nation.get("region", ""))
    if not region_id:
        return jsonify({"error": "Nation has no region"}), 400

    try:
        candidates = list(mongo.db.nations.find(
            {
                "region": region_id,
                "_id": {"$ne": nation["_id"]},
                "citizenship_stance": {"$ne": "Closed"},
            },
            {"_id": 1, "name": 1},
        ))
    except Exception:
        return jsonify({"error": "Could not search for destination nations"}), 500

    if not candidates:
        return jsonify({"error": "No eligible destination nations in this region"}), 400

    destination = random.choice(candidates)
    old_pop_data = {k: v for k, v in pop.items() if k != "_id"}
    new_pop_data = dict(old_pop_data)
    new_pop_data["nation"] = str(destination["_id"])

    change_id = system_request_change(
        data_type="pops",
        item_id=pop["_id"],
        change_type="Update",
        before_data=old_pop_data,
        after_data=new_pop_data,
        reason=(
            f"Pop manually fled from {nation.get('name', 'Unknown')} "
            f"to {destination.get('name', 'Unknown')}"
        ),
    )
    if not change_id:
        return jsonify({"error": "Failed to create change"}), 500

    system_approve_change(change_id)
    return jsonify({
        "success": True,
        "destination": destination.get("name", "Unknown"),
        "change_id": str(change_id),
    })
