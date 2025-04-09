from flask import Blueprint, render_template, session, redirect, url_for, g

base_routes = Blueprint('base_routes', __name__)

@base_routes.before_app_request
def load_user():
    g.user = session.get("user", None)

@base_routes.before_app_request
def cache_previous_url():
    from flask import request
    if request.endpoint != 'static':
        session['second_previous_url'] = session.get('previous_url', None)
        session['previous_url'] = session.get('current_url', None)
        session['current_url'] = request.url

@base_routes.route("/")
def home():
    return render_template("index.html")

@base_routes.route("/go_back")
def go_back():
    previous_url = session.get('second_previous_url', url_for("base_routes.home"))
    return redirect(previous_url)

@base_routes.context_processor
def inject_navbar_pages():
    from app_core import category_data  # Avoid circular import
    return {'category_data': category_data}

@base_routes.route("/demographics_overview")
def demographics_overview():
    from app import mongo, category_data
    from helpers.data_helpers import generate_id_to_name_dict, compute_demographics
    from pymongo import ASCENDING

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