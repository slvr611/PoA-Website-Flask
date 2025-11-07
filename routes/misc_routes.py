from flask import Blueprint, render_template, session, redirect, url_for, g, request, jsonify
from pymongo import ASCENDING
from app_core import mongo, json_data, unit_json_files, unit_json_file_titles
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
    # Create a dictionary to store categorized units with their details
    categorized_units = {}
    
    # Iterate through each unit file and its title
    for i in range(len(unit_json_files)):
        file_name = unit_json_files[i]
        category_title = unit_json_file_titles[i]
        
        # Get the units data from json_data
        units_data = json_data[file_name]
        
        # Create a list of unit details for this category
        unit_list = []
        for unit_id, unit_info in units_data.items():
            unit_details = {
                'id': unit_id,
                'name': unit_info['display_name'],
                'image_path': f'/static/images/unit_cards/{file_name}/{unit_id}.png'
            }
            unit_list.append(unit_details)
            
        # Sort units by display name
        unit_list.sort(key=lambda x: x['name'])
        
        # Add to categorized units
        categorized_units[category_title] = unit_list

    return render_template(
        "units.html",
        categorized_units=categorized_units
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
