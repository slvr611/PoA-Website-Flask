# app_core.py
from flask import Flask
from flask_pymongo import PyMongo
from flask_discord import DiscordOAuth2Session
import os
import json
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey")

# Discord config
app.config["DISCORD_CLIENT_ID"] = os.getenv("DISCORD_CLIENT_ID")
app.config["DISCORD_CLIENT_SECRET"] = os.getenv("DISCORD_CLIENT_SECRET")
app.config["DISCORD_REDIRECT_URI"] = os.getenv("DISCORD_REDIRECT_URI")

# Mongo config
app.config["MONGO_URI"] = os.getenv("MONGO_URI")
mongo = PyMongo(app)
discord = DiscordOAuth2Session(app)

category_data = {
    "nations": {"pluralName": "Nations", "singularName": "Nation", "database": mongo.db.nations},
    "regions": {"pluralName": "Regions", "singularName": "Region", "database": mongo.db.regions},
    "races": {"pluralName": "Races", "singularName": "Race", "database": mongo.db.races},
    "cultures": {"pluralName": "Cultures", "singularName": "Culture", "database": mongo.db.cultures},
    "religions": {"pluralName": "Religions", "singularName": "Religion", "database": mongo.db.religions},
    "merchants": {"pluralName": "Merchants", "singularName": "Merchant", "database": mongo.db.merchants},
    "mercenaries": {"pluralName": "Mercenaries", "singularName": "Mercenary", "database": mongo.db.mercenaries},
    "factions": {"pluralName": "Factions", "singularName": "Faction", "database": mongo.db.factions},
    "characters": {"pluralName": "Characters", "singularName": "Character", "database": mongo.db.characters},
    "players": {"pluralName": "Players", "singularName": "Player", "database": mongo.db.players},
    "artifacts": {"pluralName": "Artifacts", "singularName": "Artifact", "database": mongo.db.artifacts},
    "spells": {"pluralName": "Spells", "singularName": "Spell", "database": mongo.db.spells},
    "wonders": {"pluralName": "Wonders", "singularName": "Wonder", "database": mongo.db.wonders},
    "markets": {"pluralName": "Markets", "singularName": "Market", "database": mongo.db.markets},
    "wars": {"pluralName": "Wars", "singularName": "War", "database": mongo.db.wars},
    "war_links": {"pluralName": "War Links", "singularName": "War Link", "database": mongo.db.war_links},
    "market_links": {"pluralName": "Market Links", "singularName": "Market Link", "database": mongo.db.market_links},
    "diplo_relation": {"pluralName": "Diplomatic Relations", "singularName": "Diplomatic Relation", "database": mongo.db.diplo_relation},
    "pops": {"pluralName": "Pops", "singularName": "Pop", "database": mongo.db.pops},
    "trades": {"pluralName": "Trades", "singularName": "Trade", "database": mongo.db.trades},
    "events": {"pluralName": "Events", "singularName": "Event", "database": mongo.db.events},
    "units": {"pluralName": "Units", "singularName": "Unit", "database": mongo.db.units},
    "districts": {"pluralName": "Districts", "singularName": "District", "database": mongo.db.districts},
    "changes": {"pluralName": "Changes", "singularName": "Change", "database": mongo.db.changes}
}

json_files = ["jobs", "districts"]
json_data = {"general_resources": [
            {"key": "food", "name": "Food", "base_storage": 20},
            {"key": "wood", "name": "Wood", "base_storage": 15},
            {"key": "stone", "name": "Stone", "base_storage": 15},
            {"key": "mounts", "name": "Mounts", "base_storage": 10},
            {"key": "research", "name": "Research", "base_storage": 0},
            {"key": "magic", "name": "Magic", "base_storage": 10}
        ],
        "unique_resources": [
            {"key": "bronze", "name": "Bronze", "base_storage": 5},
            {"key": "iron", "name": "Iron", "base_storage": 0},
        ]
}

def load_json(file_path):
    with open(file_path, "r") as file:
        json_data = json.load(file)
    return json_data

for data_type in category_data:
    category_data[data_type]["schema"] = load_json("json-data/schemas/" + data_type + ".json")["$jsonSchema"]

for file in json_files:
    json_data[file] = load_json("json-data/" + file + ".json")
