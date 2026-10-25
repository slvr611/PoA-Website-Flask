from flask import Blueprint, session, redirect, url_for, request, g
from flask_discord import requires_authorization
from app_core import discord, mongo
from helpers.auth_helpers import save_user_to_db

auth_routes = Blueprint("auth_routes", __name__)


def _build_session_user(player_doc):
    """The one correct shape for session['user']/g.user, used by every
    permission check in the app (is_item_owner, gate_item_edit,
    owner_required, base_routes.calculate_user_permissions, ...): 'id' is
    always the Discord snowflake (players.id), matched back against
    mongo.db.players.find_one({"id": ...}) wherever ownership is resolved.

    Previously /refresh_user built a differently-shaped dict ('id' set to
    the player document's OWN Mongo _id instead, with the Discord snowflake
    stashed under 'discordId' instead) — if that route was ever hit, every
    players.find_one({"id": g.user.get("id")}) lookup afterward silently
    matched nothing, which is indistinguishable at the permission-check
    layer from "not logged in": gate_item_edit/is_item_owner would deny
    editing even an item the user genuinely owns, and
    calculate_user_permissions would leave view/edit_access_level at 0.
    """
    return {
        'id': player_doc['id'],
        'name': player_doc['name'],
        'avatar_url': player_doc['avatar_url'],
        'is_admin': player_doc.get('is_admin', False),
    }


@auth_routes.route("/login")
def login():
    return discord.create_session(["identify"])

@auth_routes.route("/auth/discord/callback")
def callback():
    try:
        discord.callback()
    except Exception as e:
        print(f"Error during callback: {e}")
        return "Callback error occurred", 400

    try:
        discord_user = discord.fetch_user()
        save_user_to_db(discord_user)
        user = mongo.db.players.find_one({"id": str(discord_user.id)})
        session['user'] = _build_session_user(user)
    except Exception as e:
        print(f"Error fetching user: {e}")
        return "Error fetching user information", 400

    return redirect(url_for("base_routes.home"))

@auth_routes.route("/refresh")
def refresh_token():
    try:
        discord.refresh_token()
        return redirect(url_for("base_routes.home"))
    except Exception as e:
        print(f"Error refreshing token: {e}")
        return "Failed to refresh token", 400

@auth_routes.route("/refresh_user")
def refresh_user():
    if discord.authorized:
        user = mongo.db.players.find_one({"id": str(discord.fetch_user().id)})
        if user:
            session['user'] = _build_session_user(user)
    return redirect(url_for("auth_routes.profile"))

@auth_routes.route("/logout")
def logout():
    discord.revoke()
    session.clear()
    return redirect(url_for("base_routes.home"))

@auth_routes.route("/profile")
@requires_authorization
def profile():
    if not g.user:
        return redirect(url_for("base_routes.home"))
    return redirect("/players/item/" + g.user["name"])
