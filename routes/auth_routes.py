from flask import Blueprint, session, redirect, url_for, request, g
from flask_discord import requires_authorization
from app_core import discord, mongo
from helpers.auth_helpers import save_user_to_db

auth_routes = Blueprint("auth_routes", __name__)

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
        session['user'] = {
            'id': user['id'],
            'name': user['name'],
            'avatar_url': user['avatar_url'],
            'is_admin': user['is_admin']
        }
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
        session['user'] = {
            'id': user['_id'],
            'discordId': user['id'],
            'name': user['name'],
            'avatar_url': user['avatar_url']
        }
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
