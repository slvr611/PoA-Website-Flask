from flask import Blueprint, flash, redirect, request

from app_core import mongo
from helpers.auth_helpers import admin_required
from helpers.data_helpers import get_data_on_item
from helpers.disease_helpers import cure_pop, infect_random_pops

disease_routes = Blueprint("disease_routes", __name__)


@disease_routes.route("/diseases/item/<item_ref>/infect", methods=["POST"])
@admin_required
def disease_infect(item_ref):
    """Admin seeding: infect N random uninfected pops of a nation."""
    schema, db, disease = get_data_on_item("diseases", item_ref)

    nation_id = (request.form.get("nation_id") or "").strip()
    try:
        count = max(1, int(request.form.get("count") or 1))
    except ValueError:
        count = 1

    nation = None
    if nation_id:
        from bson.objectid import ObjectId
        try:
            nation = mongo.db.nations.find_one({"_id": ObjectId(nation_id)}, {"name": 1})
        except Exception:
            nation = None
    if nation is None:
        flash("Nation not found.", "error")
        return redirect(f"/diseases/item/{item_ref}")

    if disease.get("cured"):
        flash(f"{disease.get('name', item_ref)} is cured and cannot infect new pops.", "error")
        return redirect(f"/diseases/item/{item_ref}")

    infected = infect_random_pops(nation_id, disease, count)
    if infected:
        flash(f"Infected {infected} pop(s) of {nation.get('name', nation_id)} with {disease.get('name', item_ref)}.", "success")
    else:
        flash(f"{nation.get('name', nation_id)} has no uninfected pops to infect.", "info")
    return redirect(f"/diseases/item/{item_ref}")


@disease_routes.route("/diseases/item/<item_ref>/cure_nation", methods=["POST"])
@admin_required
def disease_cure_nation(item_ref):
    """Admin correction: clear this disease from all of one nation's pops."""
    schema, db, disease = get_data_on_item("diseases", item_ref)

    nation_name = (request.form.get("nation_id") or "").strip()
    nation = mongo.db.nations.find_one({"name": nation_name}, {"_id": 1, "name": 1})
    if nation is None:
        flash("Nation not found.", "error")
        return redirect(f"/diseases/item/{item_ref}")

    cured = 0
    for pop in mongo.db.pops.find({"nation": str(nation["_id"]), "disease": str(disease["_id"])}):
        cure_pop(pop)
        cured += 1

    flash(f"Cured {cured} pop(s) of {disease.get('name', item_ref)} in {nation.get('name', nation_name)}.", "success")
    return redirect(f"/diseases/item/{item_ref}")
