from flask import Blueprint, render_template, session, redirect, url_for, g
from helpers.data_helpers import generate_id_to_name_dict, compute_demographics
from helpers.data_helpers import generate_id_to_name_dict, compute_demographics
from pymongo import ASCENDING
from app_core import mongo


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