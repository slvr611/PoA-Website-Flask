from flask import Flask, redirect, url_for, session, request, render_template, g
from flask_discord import DiscordOAuth2Session, requires_authorization, Unauthorized
from flask_pymongo import PyMongo
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Initialize the Flask app
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey")

# Discord OAuth configuration
app.config["DISCORD_CLIENT_ID"] = os.getenv("DISCORD_CLIENT_ID")
app.config["DISCORD_CLIENT_SECRET"] = os.getenv("DISCORD_CLIENT_SECRET")
app.config["DISCORD_REDIRECT_URI"] = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:5000/auth/discord/callback")

discord = DiscordOAuth2Session(app)

# MongoDB configuration
app.config["MONGO_URI"] = os.getenv("MONGO_URI", "mongodb://localhost:27017/flask_discord_app")
mongo = PyMongo(app)

# Constants
navbarPages = ["Nations", "Regions", "Races", "Cultures", "Religions", "Merchants", "Mercenaries", "Characters", "Players", "Artifacts", "Spells", "Wonders", "Markets", "Wars"]

navbarPages = {
    "nations": {"pluralName": "Nations", "singularName": "Nation", "database": mongo.db.nations},
    "regions": {"pluralName": "Regions", "singularName": "Region", "database": mongo.db.regions},
    "races": {"pluralName": "Races", "singularName": "Race", "database": mongo.db.races},
    "cultures": {"pluralName": "Cultures", "singularName": "Culture", "database": mongo.db.cultures},
    "religions": {"pluralName": "Religions", "singularName": "Religion", "database": mongo.db.religions},
    "merchants": {"pluralName": "Merchants", "singularName": "Merchant", "database": mongo.db.merchants},
    "mercenaries": {"pluralName": "Mercenaries", "singularName": "Mercenary", "database": mongo.db.mercenaries},
    "characters": {"pluralName": "Characters", "singularName": "Character", "database": mongo.db.characters},
    "players": {"pluralName": "Players", "singularName": "Player", "database": mongo.db.players},
    "artifacts": {"pluralName": "Artifacts", "singularName": "Artifact", "database": mongo.db.artifacts},
    "spells": {"pluralName": "Spells", "singularName": "Spell", "database": mongo.db.spells},
    "wonders": {"pluralName": "Wonders", "singularName": "Wonder", "database": mongo.db.wonders},
    "markets": {"pluralName": "Markets", "singularName": "Market", "database": mongo.db.markets},
    "wars": {"pluralName": "Wars", "singularName": "War", "database": mongo.db.wars}
}

@app.context_processor
def inject_navbar_pages():
    return {'navbarPages': navbarPages}

# Middleware
@app.before_request
def load_user():
    g.user = session.get('user', None)

# Routes
@app.route("/")
def home():
    return render_template("index.html")

#######################################################

@app.route("/<data_type>")
def data_list(data_type):
    if data_type not in navbarPages:
        return "Invalid data type", 404

    # Get the database collection
    db = navbarPages[data_type]["database"]

    # Fetch all documents from the collection
    items = list(db.find())

    # Pass data to the template
    return render_template(
        "dataList.html",
        title=navbarPages[data_type]["pluralName"],
        items=items
    )

#######################################################

@app.route("/<data_type>/edit")
def data_list_edit(data_type):
    if data_type not in navbarPages:
        return "Invalid data type", 404

    # Get the database collection
    db = navbarPages[data_type]["database"]

    # Fetch all documents from the collection
    items = list(db.find())

    # Pass data to the template
    return render_template(
        "dataListEdit.html",
        title=navbarPages[data_type]["pluralName"],
        items=items
    )

#######################################################

@app.route("/<data_type>/new")
def data_item_new(data_type):
    if data_type not in navbarPages:
        return "Invalid data type", 404

    # Get the database collection
    db = navbarPages[data_type]["database"]

    # Fetch all documents from the collection
    items = list(db.find())

    # Pass data to the template
    return render_template(
        "dataListEdit.html",
        title=navbarPages[data_type]["pluralName"],
        items=items
    )

#######################################################

@app.route("/<data_type>/<item_name>")
def data_item(data_type, item_name):
    if data_type not in navbarPages:
        return "Invalid data type", 404

    # Get the database collection
    db = navbarPages[data_type]["database"]

    # Fetch all documents from the collection
    item = db.find_one({"name": item_name})

    # Pass data to the template
    return render_template(
        "dataItem.html",
        title=navbarPages[data_type]["pluralName"],
        item=item
    )

#######################################################

@app.route("/<data_type>/<item_name>/edit")
def data_item_edit(data_type, item_name):
    if data_type not in navbarPages:
        return "Invalid data type", 404

    # Get the database collection
    db = navbarPages[data_type]["database"]

    # Fetch all documents from the collection
    item = db.find_one({"name": item_name})

    # Pass data to the template
    return render_template(
        "dataItemEdit.html",
        title=navbarPages[data_type]["pluralName"],
        item=item
    )

#######################################################

@app.route("/<data_type>/<item_name>/delete")
def data_item_delete(data_type, item_name):
    if data_type not in navbarPages:
        return "Invalid data type", 404

    # Get the database collection
    db = navbarPages[data_type]["database"]

    # Fetch all documents from the collection
    db.delete_one({"name": item_name})

    # Pass data to the template
    return redirect("/" + data_type)

#######################################################

@app.route("/login")
def login():
    return discord.create_session(["identify"])

@app.route("/auth/discord/callback")
def callback():
    try:
        discord.callback()
    except Exception as e:
        print(f"Error during callback: {e}")
        return "Callback error occurred", 400
    
    try:
        discordUser = discord.fetch_user()
        save_user_to_db(discordUser)
        user = mongo.db.players.find_one({"id": str(discordUser.id)})
        session['user'] = {
            'id': user['id'],
            'name': user['name'],
            'avatar_url': user['avatar_url'],
            'isAdmin': user['isAdmin']
        }
    except Exception as e:
        print(f"Error fetching user: {e}")
        return "Error fetching user information", 400
    
    return redirect(url_for("home"))

@app.route("/refresh")
def refresh_token():
    try:
        discord.refresh_token()
        return redirect(url_for("home"))
    except Exception as e:
        print(f"Error refreshing token: {e}")
        return "Failed to refresh token", 400

@app.route("/refresh_user")
def refresh_user():
    if discord.authorized:
        user = mongo.db.players.find_one({"id": str(discord.fetch_user().id)})
        session['user'] = {
            'id': user['_id'],
            'discordId': user['id'],
            'name': user['name'],
            'avatar_url': user['avatar_url']
        }
    return redirect(url_for("profile"))

@app.route("/logout")
def logout():
    discord.revoke()
    session.clear()
    return redirect(url_for("home"))

@app.route("/profile")
@requires_authorization
def profile():
    if(g.user is None):
        return redirect(url_for("home"))
    return render_template("profile.html")

# Helper function to save user to MongoDB
def save_user_to_db(user):
    db = mongo.db
    
    existing_player = db.players.find_one({"id": str(user.id)})
    if not existing_player:
        db.players.insert_one({
            "id": str(user.id),
            "name": user.name,
            "avatar_url": user.avatar_url,
            "isAdmin": False
        })
        print(f"Player {user.name} added to database.")
    else:
        db.players.update_one(
            {"id": str(user.id)},
            {"$set": {
                "name": user.name,
                "avatar_url": user.avatar_url
            }}
        )
        print(f"Player {user.name} updated in database.")

##############################################################

if __name__ == "__main__":
    app.run(debug=True)
