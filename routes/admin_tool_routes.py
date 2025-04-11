from flask import Blueprint, render_template, redirect
from helpers.auth_helpers import admin_required
from helpers.data_helpers import get_data_on_category
from calculations.field_calculations import calculate_all_fields
from app_core import category_data, rarity_rankings
from pymongo import ASCENDING
import random

admin_tool_routes = Blueprint('admin_tool_routes', __name__)

@admin_tool_routes.route("/admin_tools")
@admin_required
def admin_tools():
    return render_template("admin_tools.html")

@admin_tool_routes.route("/karma_helper")
@admin_required
def karma_helper():
    schema, db = get_data_on_category("nations")

    player_nations = list(db.find({"temperament": "Player"}).sort("name", ASCENDING))
    ai_nations = list(db.find({"temperament": {"$ne": "Player"}}).sort("name", ASCENDING))

    for nation in player_nations + ai_nations:
        calculated_fields = calculate_all_fields(nation, schema)
        nation.update(calculated_fields)

    return render_template("karma_helper.html",
                           player_nations=player_nations,
                           ai_nations=ai_nations)

@admin_tool_routes.route("/roll_karma")
@admin_required
def roll_karma():
    schema, db = get_data_on_category("nations")

    nations = list(db.find().sort("name", ASCENDING))

    for nation in nations:
        calculated_fields = calculate_all_fields(nation, schema)
        nation.update(calculated_fields)
        raw_roll = random.randint(1, 20)
        event_roll = raw_roll + nation.get("karma", 0)
        event_type = "Unknown"
        if raw_roll == 20 and event_roll >= 30:
            event_type = "Wonderous"
        elif raw_roll == 20 or event_roll >= 23:
            event_type = "Fantastic"
        elif event_roll >= 18:
            event_type = "Very Good"
        elif event_roll >= 15:
            event_type = "Good"
        elif event_roll >= 7:
            event_type = "Neutral"
        elif event_roll >= 5:
            event_type = "Bad"
        elif event_roll >= 2:
            event_type = "Very Bad"
        elif event_roll <= 1:
            event_type = "Abysmal"
        elif event_roll <= -10:
            event_type = "Horrendous"

        nation.update({"temp_karma": 0, "event_roll": event_roll, "raw_roll": raw_roll, "event_type": event_type})
        db.update_one({"_id": nation["_id"]}, {"$set": nation})
    
    return redirect("/karma_helper")