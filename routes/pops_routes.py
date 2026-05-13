from flask import Blueprint, render_template, request, jsonify, g
from app_core import mongo
from helpers.auth_helpers import admin_required
from helpers.change_helpers import request_change, approve_change
from pymongo import ASCENDING
from bson import ObjectId

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
        }
        for n in mongo.db.nations.find(
            {}, {"_id": 1, "name": 1, "primary_race": 1, "primary_culture": 1, "primary_religion": 1}
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
