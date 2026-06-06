from flask import Blueprint, render_template, session, redirect, url_for, g, send_from_directory, after_this_request, request, Response, abort
from pymongo import ASCENDING
from app_core import mongo
import datetime
import os
import boto3


base_routes = Blueprint('base_routes', __name__)

@base_routes.before_app_request
def inject_now():
    g.now = datetime.datetime.now()

@base_routes.before_app_request
def load_user():
    g.user = session.get("user", None)

@base_routes.before_app_request
def calculate_user_permissions():
    # Default permission levels
    g.view_access_level = 0  # Public access
    g.edit_access_level = 0  # No edit access
    g.is_non_player_admin = False

    if g.user:
        # Get user from database to check permissions
        user = mongo.db.players.find_one({"id": g.user.get("id")})

        if user:
            # Base view access for authenticated users
            g.view_access_level = 5

            # Check if user is non-player admin (RP mod with auto visibility bypass)
            if user.get("is_non_player_admin", False):
                g.is_non_player_admin = True

            # Check if user is admin
            if user.get("is_admin", False):
                g.view_access_level = 10  # Admin view access
                g.edit_access_level = 10  # Admin edit access
                return

            if g.is_non_player_admin:
                g.view_access_level = 7
                return

            # Check if user owns any nations/organizations
            user_id = str(user.get("_id"))
            user_characters = list(mongo.db.characters.find({"player": user_id}))

@base_routes.before_app_request
def cache_previous_url():
    if request.endpoint != 'static':
        session['second_previous_url'] = session.get('previous_url', None)
        session['previous_url'] = session.get('current_url', None)
        session['current_url'] = request.url

@base_routes.route("/")
def home():
    global_modifiers = mongo.db.global_modifiers.find_one({"name": "global_modifiers"})
    if not global_modifiers:
        global_modifiers = {"name": "global_modifiers"}
        mongo.db.global_modifiers.insert_one(global_modifiers)
    current_session = global_modifiers.get("session_counter", 1)
    return render_template("index.html", current_session=current_session)

@base_routes.route("/map")
def map():
    global_modifiers = mongo.db.global_modifiers.find_one({"name": "global_modifiers"})
    if not global_modifiers:
        global_modifiers = {"name": "global_modifiers"}
        mongo.db.global_modifiers.insert_one(global_modifiers)
    current_session = global_modifiers.get("session_counter", 1)
    return render_template("map.html", current_session=current_session)

@base_routes.route("/go_back")
def go_back():
    previous_url = session.get('second_previous_url', url_for("base_routes.home"))
    return redirect(previous_url)

@base_routes.route('/api/s3-image/<path:s3_key>')
def s3_image_proxy(s3_key):
    """Serve images stored in the private S3 bucket through the Flask app."""
    s3_bucket     = os.getenv("S3_BUCKET_NAME")
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

    if not all([s3_bucket, aws_access_key, aws_secret_key]):
        abort(503)

    try:
        client = boto3.client(
            "s3",
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
        )
        obj = client.get_object(Bucket=s3_bucket, Key=s3_key)
        content_type = obj.get("ContentType", "application/octet-stream")
        return Response(
            obj["Body"].read(),
            mimetype=content_type,
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception:
        abort(404)


@base_routes.route('/static/images/maps/<path:filename>')
def optimized_map_serving(filename):
    """Serve map files with optimized caching headers"""
    @after_this_request
    def add_header(response):
        # Cache for 1 week
        response.headers['Cache-Control'] = 'public, max-age=604800'
        return response
    
    # Path to your static files
    maps_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'images', 'maps')
    return send_from_directory(maps_dir, filename)
