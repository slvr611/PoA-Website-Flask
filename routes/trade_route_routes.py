import json
import math
from flask import Blueprint, render_template, redirect, request, flash, url_for, g
from app_core import mongo, category_data
from helpers.auth_helpers import admin_required
from helpers.trade_route_helpers import (
    get_road_path_distance,
    compute_delay,
    count_route_slots,
    _current_session,
)

trade_route_routes = Blueprint("trade_route_routes", __name__)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _player_owns_nation(nation_name):
    """True if the current user owns the named nation via a ruling character."""
    if not g.user:
        return False
    user = mongo.db.players.find_one({"id": g.user.get("id")})
    if not user:
        return False
    nation = mongo.db.nations.find_one({"name": nation_name}, {"_id": 1})
    if not nation:
        return False
    return bool(mongo.db.characters.find_one({
        "player": str(user["_id"]),
        "ruling_nation_org": str(nation["_id"]),
    }))


def _can_act_on_nation(nation_name):
    return bool(g.user and g.user.get("is_admin")) or _player_owns_nation(nation_name)


# ---------------------------------------------------------------------------
# Propose
# ---------------------------------------------------------------------------

@trade_route_routes.route("/trade_routes/propose", methods=["POST"])
def propose_trade_route():
    proposer = request.form.get("proposer_nation", "").strip()
    acceptor = request.form.get("acceptor_nation", "").strip()

    if not _can_act_on_nation(proposer):
        flash("You do not have permission to propose a trade route for that nation.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    if proposer == acceptor:
        flash("A nation cannot trade with itself.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    # Parse resources
    try:
        resources_a_to_b = json.loads(request.form.get("resources_a_to_b", "[]"))
        resources_b_to_a = json.loads(request.form.get("resources_b_to_a", "[]"))
    except (ValueError, TypeError):
        flash("Invalid resource list.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    resources_a_to_b = [
        {"resource": e["resource"], "quantity": int(e.get("quantity", 0))}
        for e in resources_a_to_b
        if e.get("resource") and int(e.get("quantity", 0)) > 0
    ]
    resources_b_to_a = [
        {"resource": e["resource"], "quantity": int(e.get("quantity", 0))}
        for e in resources_b_to_a
        if e.get("resource") and int(e.get("quantity", 0)) > 0
    ]

    if not resources_a_to_b and not resources_b_to_a:
        flash("A trade route must include at least one resource.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    duration_raw = request.form.get("duration_ticks", "0").strip()
    try:
        duration_ticks = int(duration_raw)
        if duration_ticks <= 0:
            duration_ticks = None
    except ValueError:
        duration_ticks = None

    proposal_note = request.form.get("proposal_note", "").strip() or None

    # Check road connectivity
    dist, connected = get_road_path_distance(proposer, acceptor)
    if not connected:
        flash(f"No road connection found between {proposer} and {acceptor}.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    # Compute delay
    nation_a_doc = mongo.db.nations.find_one({"name": proposer}, {"trade_speed": 1, "temperament": 1, "export_slots": 1, "import_slots": 1, "players": 1})
    nation_b_doc = mongo.db.nations.find_one({"name": acceptor}, {"trade_speed": 1, "temperament": 1, "players": 1})
    speed_a = (nation_a_doc or {}).get("trade_speed") or 7
    speed_b = (nation_b_doc or {}).get("trade_speed") or 7
    delay = compute_delay(dist, speed_a, speed_b)

    # Check export slots for proposer (a_to_b direction)
    from helpers.trade_route_helpers import _nations_share_market, _slot_cost_for_direction
    capacity = 4 if _nations_share_market(proposer, acceptor) else 2

    if resources_a_to_b:
        export_used, _ = count_route_slots(proposer)
        export_cap = (nation_a_doc or {}).get("export_slots", 3)
        new_export_cost = _slot_cost_for_direction(resources_a_to_b, capacity)
        if export_used + new_export_cost > export_cap:
            flash(f"{proposer} does not have enough export slots for this route.", "danger")
            return redirect(request.referrer or url_for("base_routes.index"))

    if resources_b_to_a:
        _, import_used = count_route_slots(proposer)
        import_cap = (nation_a_doc or {}).get("import_slots", 3)
        new_import_cost = _slot_cost_for_direction(resources_b_to_a, capacity)
        if import_used + new_import_cost > import_cap:
            flash(f"{proposer} does not have enough import slots for this route.", "danger")
            return redirect(request.referrer or url_for("base_routes.index"))

    current_session = _current_session()

    route_doc = {
        "nation_a": proposer,
        "nation_b": acceptor,
        "proposer": proposer,
        "status": "pending",
        "resources_a_to_b": resources_a_to_b,
        "resources_b_to_a": resources_b_to_a,
        "road_distance": dist,
        "delay": delay,
        "duration_ticks": duration_ticks,
        "created_session": current_session,
        "accepted_session": None,
        "cancel_session": None,
        "cancelled_by": None,
        "ended_session": None,
        "proposal_note": proposal_note,
    }

    # Check if either nation is AI (not assigned to a player).
    # A nation is "AI" if it has no ruling character with a player, and no
    # entries in its players array.
    def _is_ai_nation(nation_doc):
        if not nation_doc:
            return True
        if nation_doc.get("players"):
            return False
        nid = str(nation_doc.get("_id", ""))
        if nid:
            char = mongo.db.characters.find_one({
                "ruling_nation_org": nid,
                "player": {"$exists": True, "$ne": None, "$ne": ""},
            }, {"_id": 1})
            if char:
                return False
        return True

    involves_ai = _is_ai_nation(nation_a_doc) or _is_ai_nation(nation_b_doc)
    if involves_ai:
        from helpers.change_helpers import request_change
        change_reason = f"Trade route proposed by {proposer} to {acceptor}"
        if proposal_note:
            change_reason += f": {proposal_note}"
        change_id = request_change(
            data_type="trade_routes",
            item_id=None,
            change_type="Add",
            before_data={},
            after_data=route_doc,
            reason=change_reason,
        )
        if change_id:
            flash(f"Trade route involving an AI nation submitted for moderator approval (change #{change_id}).", "info")
        return redirect(request.referrer or url_for("nation_routes.nation_item", item_ref=proposer))

    mongo.db.trade_routes.insert_one(route_doc)
    flash(f"Trade route proposed to {acceptor}. Awaiting their acceptance.", "success")
    return redirect(request.referrer or url_for("nation_routes.nation_item", item_ref=proposer))


# ---------------------------------------------------------------------------
# Accept
# ---------------------------------------------------------------------------

@trade_route_routes.route("/trade_routes/<route_id>/accept", methods=["POST"])
def accept_trade_route(route_id):
    from bson import ObjectId
    try:
        oid = ObjectId(route_id)
    except Exception:
        flash("Invalid route ID.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    route = mongo.db.trade_routes.find_one({"_id": oid})
    if not route:
        flash("Trade route not found.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    if route.get("status") != "pending":
        flash("This route is not pending acceptance.", "warning")
        return redirect(request.referrer or url_for("base_routes.index"))

    acceptor = route["nation_b"]
    if not _can_act_on_nation(acceptor):
        flash("You do not have permission to accept this trade route.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    # Slot check for acceptor
    resources_a_to_b = route.get("resources_a_to_b", [])
    resources_b_to_a = route.get("resources_b_to_a", [])
    if resources_b_to_a or resources_a_to_b:
        from helpers.trade_route_helpers import _nations_share_market, _slot_cost_for_direction
        na, nb = route["nation_a"], route["nation_b"]
        capacity = 4 if _nations_share_market(na, nb) else 2
        _, import_used = count_route_slots(acceptor)
        export_used, _ = count_route_slots(acceptor)
        acceptor_doc = mongo.db.nations.find_one({"name": acceptor}, {"import_slots": 1, "export_slots": 1})
        import_cap = (acceptor_doc or {}).get("import_slots", 3)
        export_cap = (acceptor_doc or {}).get("export_slots", 3)

        new_import = _slot_cost_for_direction(resources_a_to_b, capacity)
        new_export = _slot_cost_for_direction(resources_b_to_a, capacity)

        if import_used + new_import > import_cap:
            flash(f"{acceptor} does not have enough import slots.", "danger")
            return redirect(request.referrer or url_for("base_routes.index"))
        if export_used + new_export > export_cap:
            flash(f"{acceptor} does not have enough export slots.", "danger")
            return redirect(request.referrer or url_for("base_routes.index"))

    current_session = _current_session()
    mongo.db.trade_routes.update_one(
        {"_id": oid},
        {"$set": {"status": "active", "accepted_session": current_session}},
    )

    flash(f"Trade route with {route['nation_a']} accepted.", "success")
    return redirect(request.referrer or url_for("nation_routes.nation_item", item_ref=acceptor))


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------

@trade_route_routes.route("/trade_routes/<route_id>/reject", methods=["POST"])
def reject_trade_route(route_id):
    from bson import ObjectId
    try:
        oid = ObjectId(route_id)
    except Exception:
        flash("Invalid route ID.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    route = mongo.db.trade_routes.find_one({"_id": oid})
    if not route:
        flash("Trade route not found.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    if route.get("status") != "pending":
        flash("This route is not pending.", "warning")
        return redirect(request.referrer or url_for("base_routes.index"))

    rejector = route["nation_b"]
    if not _can_act_on_nation(rejector):
        flash("You do not have permission to reject this trade route.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    mongo.db.trade_routes.update_one({"_id": oid}, {"$set": {"status": "rejected"}})
    flash(f"Trade route from {route['nation_a']} rejected.", "success")
    return redirect(request.referrer or url_for("nation_routes.nation_item", item_ref=rejector))


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

@trade_route_routes.route("/trade_routes/<route_id>/cancel", methods=["POST"])
def cancel_trade_route(route_id):
    from bson import ObjectId
    try:
        oid = ObjectId(route_id)
    except Exception:
        flash("Invalid route ID.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    route = mongo.db.trade_routes.find_one({"_id": oid})
    if not route:
        flash("Trade route not found.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    if route.get("status") not in ("pending", "active", "ending"):
        flash("This route cannot be cancelled.", "warning")
        return redirect(request.referrer or url_for("base_routes.index"))

    canceller = request.form.get("cancelling_nation", "").strip()
    if canceller not in (route["nation_a"], route["nation_b"]):
        flash("Invalid cancellation request.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    if not _can_act_on_nation(canceller):
        flash("You do not have permission to cancel this trade route.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    # Withdrawing a pending proposal is always instant — it was never accepted
    if route.get("status") == "pending":
        mongo.db.trade_routes.update_one({"_id": oid}, {"$set": {"status": "rejected", "cancelled_by": canceller}})
        flash("Trade route proposal withdrawn.", "success")
        return redirect(request.referrer or url_for("nation_routes.nation_item", item_ref=canceller))

    current_session = _current_session()
    delay = route.get("delay", 0)

    if delay == 0:
        # Instant cancellation — mark ended immediately
        new_status = "ended"
        update = {
            "status": "ended",
            "cancel_session": current_session,
            "cancelled_by": canceller,
            "ended_session": current_session,
        }
    else:
        new_status = "ending"
        update = {
            "status": "ending",
            "cancel_session": current_session,
            "cancelled_by": canceller,
        }

    mongo.db.trade_routes.update_one({"_id": oid}, {"$set": update})

    if new_status == "ended":
        flash("Trade route cancelled immediately.", "success")
    else:
        from helpers.trade_route_helpers import last_delivery_session
        lds = last_delivery_session({**route, **update})
        flash(
            f"Trade route cancellation requested. Final delivery will be session {lds}.",
            "success",
        )

    return redirect(request.referrer or url_for("nation_routes.nation_item", item_ref=canceller))


# ---------------------------------------------------------------------------
# Admin list view
# ---------------------------------------------------------------------------

@trade_route_routes.route("/trade_routes")
@admin_required
def list_trade_routes():
    routes = list(mongo.db.trade_routes.find().sort("created_session", -1))
    current_session = _current_session()
    return render_template(
        "trade_routes_admin.html",
        routes=routes,
        current_session=current_session,
    )
