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
# Party helpers — a trade route party is either a nation or a merchant
# company, disambiguated by nation_a_type/nation_b_type (both default to
# "nation" for backward compatibility with routes created before merchants
# could participate).
# ---------------------------------------------------------------------------

def _party_collection(party_type):
    return mongo.db.merchants if party_type == "merchant" else mongo.db.nations


def _party_item_url(party_type, party_name):
    prefix = "merchants" if party_type == "merchant" else "nations"
    return f"/{prefix}/item/{party_name}"


def _resolve_party_doc(party_type, party_name, projection=None):
    return _party_collection(party_type).find_one({"name": party_name}, projection)


def _player_owns_nation(nation_name):
    """True if the current user owns the named nation, either via a ruling
    character (ruling_nation_org) or direct attribution (nation.players) —
    same two ownership paths get_viewer_nations checks elsewhere in the app."""
    if not g.user:
        return False
    user = mongo.db.players.find_one({"id": g.user.get("id")})
    if not user:
        return False
    nation = mongo.db.nations.find_one({"name": nation_name}, {"_id": 1, "players": 1})
    if not nation:
        return False
    player_id = str(user["_id"])
    if player_id in (nation.get("players") or []):
        return True
    return bool(mongo.db.characters.find_one({
        "player": player_id,
        "ruling_nation_org": str(nation["_id"]),
    }))


def _player_owns_merchant(merchant_name):
    """True if the current user controls the named merchant company via a
    character ruling it (character.ruling_nation_org == merchant._id — the
    same polymorphic field nations use, per helpers.visibility_helpers.
    is_item_owner's merchants branch). Delegates to is_item_owner so both
    call sites stay in lockstep with that ownership rule."""
    if not g.user:
        return False
    merchant = mongo.db.merchants.find_one({"name": merchant_name}, {"_id": 1})
    if not merchant:
        return False
    from helpers.visibility_helpers import is_item_owner
    return is_item_owner("merchants", merchant, g.user)


def _can_act_on_party(party_type, party_name):
    if bool(g.user and g.user.get("is_admin")):
        return True
    if party_type == "merchant":
        return _player_owns_merchant(party_name)
    return _player_owns_nation(party_name)


def _is_ai_party(party_type, doc):
    """True if no real player controls this party — same admin-approval
    gate nations already get, generalized to merchants (a merchant with no
    player-controlled ruling character counts as AI). Both branches check
    for a character with ruling_nation_org == this party's _id and a real
    player attached — nations and merchants share that same polymorphic
    rulership field."""
    if not doc:
        return True
    if party_type == "merchant":
        merchant_id = str(doc.get("_id", ""))
        if not merchant_id:
            return True
        char = mongo.db.characters.find_one({
            "ruling_nation_org": merchant_id,
            "player": {"$exists": True, "$nin": [None, ""]},
        }, {"_id": 1})
        return char is None
    if doc.get("players"):
        return False
    nid = str(doc.get("_id", ""))
    if nid:
        char = mongo.db.characters.find_one({
            "ruling_nation_org": nid,
            "player": {"$exists": True, "$nin": [None, ""]},
        }, {"_id": 1})
        if char:
            return False
    return True


# ---------------------------------------------------------------------------
# Propose
# ---------------------------------------------------------------------------

@trade_route_routes.route("/trade_routes/propose", methods=["POST"])
def propose_trade_route():
    proposer = request.form.get("proposer_nation", "").strip()
    acceptor = request.form.get("acceptor_nation", "").strip()
    proposer_type = request.form.get("proposer_type", "nation").strip() or "nation"
    acceptor_type = request.form.get("acceptor_type", "nation").strip() or "nation"

    if not _can_act_on_party(proposer_type, proposer):
        flash("You do not have permission to propose a trade route for that party.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    if proposer == acceptor and proposer_type == acceptor_type:
        flash("A party cannot trade with itself.", "danger")
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
    dist, connected = get_road_path_distance(
        proposer, acceptor, party_a_type=proposer_type, party_b_type=acceptor_type,
    )
    if not connected:
        flash(f"No road connection found between {proposer} and {acceptor}.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    # Compute delay
    nation_a_doc = _resolve_party_doc(proposer_type, proposer, {"trade_speed": 1, "temperament": 1, "export_slots": 1, "import_slots": 1, "players": 1})
    nation_b_doc = _resolve_party_doc(acceptor_type, acceptor, {"trade_speed": 1, "temperament": 1, "players": 1})
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
        "nation_a_type": proposer_type,
        "nation_b_type": acceptor_type,
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

    # Check if either party is AI (not controlled by a real player) —
    # such routes go to moderator approval instead of taking effect directly.
    involves_ai = _is_ai_party(proposer_type, nation_a_doc) or _is_ai_party(acceptor_type, nation_b_doc)
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
            flash(f"Trade route involving an AI party submitted for moderator approval (change #{change_id}).", "info")
        return redirect(request.referrer or _party_item_url(proposer_type, proposer))

    mongo.db.trade_routes.insert_one(route_doc)
    flash(f"Trade route proposed to {acceptor}. Awaiting their acceptance.", "success")
    return redirect(request.referrer or _party_item_url(proposer_type, proposer))


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
    acceptor_type = route.get("nation_b_type", "nation")
    if not _can_act_on_party(acceptor_type, acceptor):
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
        acceptor_doc = _resolve_party_doc(acceptor_type, acceptor, {"import_slots": 1, "export_slots": 1})
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
    return redirect(request.referrer or _party_item_url(acceptor_type, acceptor))


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
    rejector_type = route.get("nation_b_type", "nation")
    if not _can_act_on_party(rejector_type, rejector):
        flash("You do not have permission to reject this trade route.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    mongo.db.trade_routes.update_one({"_id": oid}, {"$set": {"status": "rejected"}})
    flash(f"Trade route from {route['nation_a']} rejected.", "success")
    return redirect(request.referrer or _party_item_url(rejector_type, rejector))


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
    if canceller == route["nation_a"]:
        canceller_type = route.get("nation_a_type", "nation")
    elif canceller == route["nation_b"]:
        canceller_type = route.get("nation_b_type", "nation")
    else:
        flash("Invalid cancellation request.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    if not _can_act_on_party(canceller_type, canceller):
        flash("You do not have permission to cancel this trade route.", "danger")
        return redirect(request.referrer or url_for("base_routes.index"))

    # Withdrawing a pending proposal is always instant — it was never accepted
    if route.get("status") == "pending":
        mongo.db.trade_routes.update_one({"_id": oid}, {"$set": {"status": "rejected", "cancelled_by": canceller}})
        flash("Trade route proposal withdrawn.", "success")
        return redirect(request.referrer or _party_item_url(canceller_type, canceller))

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

    return redirect(request.referrer or _party_item_url(canceller_type, canceller))


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
