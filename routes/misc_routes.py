from flask import Blueprint, render_template, session, redirect, url_for, g, request, jsonify
from pymongo import ASCENDING
from app_core import mongo, json_data, category_data
from helpers.data_helpers import generate_id_to_name_dict, compute_demographics


misc_routes = Blueprint('misc_routes', __name__)

@misc_routes.route('/get_available_slots', methods=['POST'])
def get_available_slots():
    try:
        # Get form data and convert to nation data format
        nation_data = {}
        for key, value in request.form.items():
            if not key.startswith('progress_quests-') and not key.startswith('modifiers-'):
                nation_data[key] = value

        # Load schema
        schema = json_data["nations"]["schema"]

        # Create a temporary form instance to use get_available_slots method
        from forms import NationForm
        form = NationForm()
        available_slots = form.get_available_slots(nation_data, schema)

        return jsonify(available_slots)
    except Exception as e:
        return jsonify([("no_slot", "No Slot")]), 500

@misc_routes.route("/units")
def units():
    units_db = category_data["units"]["database"]
    all_units = list(units_db.find().sort("name", ASCENDING))

    era_order = ["Tribal", "Ancient", "Classical", "Medieval", "Industrial", "Modern", "Crisis"]
    type_order = ["Land", "Support", "Naval", "Other"]

    # Separate ruler units from the rest
    ruler_units = [u for u in all_units if u.get("unit_class") == "Ruler Unit"]
    regular_units = [u for u in all_units if u.get("unit_class") != "Ruler Unit"]

    # Nested grouping: {era: {unit_type: [units]}}
    # Support land units are grouped as "Support" rather than "Land"
    grouped = {}
    for unit in regular_units:
        era = unit.get("era") or "Other"
        if unit.get("unit_type") == "Land" and unit.get("support"):
            utype = "Support"
        else:
            utype = unit.get("unit_type") or "Other"
        grouped.setdefault(era, {}).setdefault(utype, [])
        grouped[era][utype].append(unit)

    # Sort eras and types by defined order, unknowns at the end
    sorted_grouped = {}
    for era in era_order:
        if era not in grouped:
            continue
        type_dict = grouped[era]
        sorted_types = {t: type_dict[t] for t in type_order if t in type_dict}
        for t in type_dict:
            if t not in sorted_types:
                sorted_types[t] = type_dict[t]
        sorted_grouped[era] = sorted_types
    for era in grouped:
        if era not in sorted_grouped:
            sorted_grouped[era] = grouped[era]

    # Build a lookup dict so the list page can show trait descriptions inline
    all_traits = list(category_data["traits"]["database"].find(
        {}, {"name": 1, "cost": 1, "description": 1}
    ))
    traits_lookup = {t["name"]: t for t in all_traits}

    district_files = ["nation_districts", "nation_imperial_districts", "mercenary_districts",
                      "merchant_production_districts", "merchant_specialty_districts", "merchant_luxury_districts"]
    districts_lookup = {}
    for fname in district_files:
        for key, data in json_data.get(fname, {}).items():
            districts_lookup[key] = data.get("display_name", key)

    tech_lookup = {key: data.get("display_name", key) for key, data in json_data.get("tech", {}).items()}

    def _names(col):
        return {d["name"] for d in category_data[col]["database"].find({}, {"_id": 0, "name": 1})}

    return render_template(
        "units.html",
        grouped_units=sorted_grouped,
        ruler_units=ruler_units,
        traits_lookup=traits_lookup,
        districts_lookup=districts_lookup,
        tech_lookup=tech_lookup,
        mercenaries_names=_names("mercenaries"),
        races_names=_names("races"),
    )

@misc_routes.route("/demographics_overview")
def demographics_overview():
    nations = list(mongo.db.nations.find().sort("name", ASCENDING))

    race_id_to_name = generate_id_to_name_dict("races")
    culture_id_to_name = generate_id_to_name_dict("cultures")
    religion_id_to_name = generate_id_to_name_dict("religions")

    demographics_list = []
    for nation in nations:
        demo = compute_demographics(nation.get("_id", None), race_id_to_name, culture_id_to_name, religion_id_to_name)
        demographics_list.append({
            "name": nation["name"],
            "demographics": demo
        })

    return render_template("demographics_overview.html", demographics_list=demographics_list)
