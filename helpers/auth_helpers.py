import os
import hmac
from functools import wraps
from flask import g, redirect, url_for, flash, request, jsonify
from app_core import mongo

def api_key_required(f):
    """Gate a JSON API route behind a shared-secret key (BOT_API_KEY in .env),
    sent as the X-API-Key header. For external integrations (e.g. a Discord
    bot) that have no browser session to authenticate with. Fails closed: if
    BOT_API_KEY isn't configured, every request is rejected rather than
    silently allowed through."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        expected_key = os.getenv("BOT_API_KEY", "")
        provided_key = request.headers.get("X-API-Key", "")
        if not expected_key or not hmac.compare_digest(provided_key, expected_key):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not g.user or not g.user.get("is_admin", False):
            flash("You must be an admin to access this page.")
            return redirect(url_for("base_routes.home"))
        return f(*args, **kwargs)
    return decorated_function

def owner_required(item_type):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not g.user:
                flash("Not logged in.")
                return redirect(url_for("base_routes.home"))

            # Get the item_ref from the URL parameters
            item_ref = kwargs.get('item_ref')
            if not item_ref:
                flash("Invalid request.")
                return redirect(url_for("base_routes.home"))

            user_id = str(mongo.db.players.find_one({"id": g.user.get("id")})["_id"])
            if not user_id:
                flash("Invalid user.")
                return redirect(url_for("base_routes.home"))
            
            user_characters = list(mongo.db.characters.find({"player": user_id}))
            user_nations_orgs = []
            for character in user_characters:
                if character.get("ruling_nation_org"):
                    user_nations_orgs.append(character.get("ruling_nation_org"))

            if item_type == "characters":
                character = mongo.db.characters.find_one({"name": item_ref})
                if not character or str(character.get("player")) != str(g.user.get("id")):
                    flash("You don't have permission to access this character.")
                    return redirect(url_for("base_routes.home"))

            elif item_type == "nations" or item_type == "organizations":
                nation = mongo.db.nations.find_one({"name": item_ref})
                # A nation can be owned two ways: via a ruling character
                # (user_nations_orgs, built above) OR direct attribution in
                # nation.players — a nation with no ruling character at all
                # still needs this second path checked, same as
                # helpers.visibility_helpers.is_item_owner and
                # calculations.visibility.get_viewer_nations already do.
                # This branch previously only checked the first path, so a
                # directly-attributed player with no ruling character was
                # wrongly denied access to their own nation.
                has_access = bool(nation) and (
                    str(nation.get("_id")) in user_nations_orgs
                    or user_id in nation.get("players", [])
                )
                if not has_access:
                    flash("You don't have permission to access this nation.")
                    return redirect(url_for("base_routes.home"))

            return f(*args, **kwargs)
        return decorated_function
    return decorator

def save_user_to_db(user):
    from app_core import mongo

    existing_player = mongo.db.players.find_one({"id": str(user.id)})
    if not existing_player:
        mongo.db.players.insert_one({
            "id": str(user.id),
            "name": user.name,
            "avatar_url": user.avatar_url,
            "is_admin": False,
            "is_rp_mod": False,
            "is_website_helper": False
        })
        print(f"Player {user.name} added to database.")
    else:
        mongo.db.players.update_one(
            {"id": str(user.id)},
            {"$set": {
                "name": user.name,
                "avatar_url": user.avatar_url
            }}
        )
        print(f"Player {user.name} updated in database.")
