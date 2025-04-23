from flask import Blueprint, render_template, session, redirect, url_for, g
from pymongo import ASCENDING
from app_core import mongo, json_data, unit_json_files, unit_json_file_titles


misc_routes = Blueprint('misc_routes', __name__)

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
