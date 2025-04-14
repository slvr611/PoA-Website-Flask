from flask import Blueprint, render_template, redirect
from helpers.auth_helpers import admin_required
from helpers.data_helpers import get_data_on_category, generate_id_to_name_dict, compute_demographics
from calculations.field_calculations import calculate_all_fields
from app_core import category_data, rarity_rankings, mongo, json_data
from pymongo import ASCENDING
import random

admin_tool_routes = Blueprint('admin_tool_routes', __name__)

@admin_tool_routes.route("/admin_tools")
@admin_required
def admin_tools():
    return render_template("admin_tools.html")

@admin_tool_routes.route("/demographics_overview")
@admin_required
def demographics_overview():
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

@admin_tool_routes.route("/elected_candidates_generator")
@admin_required
def elected_candidates_generator():
    stats_list = ["Rulership", "Cunning", "Charisma", "Prowess", "Magic", "Strategy"]

    candidate_1_stats_list = stats_list.copy()

    candidate_1_strengths = [candidate_1_stats_list.pop(random.randint(0, len(candidate_1_stats_list) - 1))]
    candidate_1_strengths.append(candidate_1_stats_list.pop(random.randint(0, len(candidate_1_stats_list) - 1)))
    candidate_1_weaknesses = [candidate_1_stats_list.pop(random.randint(0, len(candidate_1_stats_list) - 1))]
    candidate_1_weaknesses.append(candidate_1_stats_list.pop(random.randint(0, len(candidate_1_stats_list) - 1)))

    candidate_2_strengths = []
    candidate_2_weaknesses = []

    candidate_2_stats_list = stats_list.copy()
    candidate_2_strengths = [candidate_2_stats_list.pop(random.randint(0, len(candidate_2_stats_list) - 1))]
    candidate_2_strengths.append(candidate_2_stats_list.pop(random.randint(0, len(candidate_2_stats_list) - 1)))

    while candidate_2_strengths == candidate_1_strengths:
        candidate_2_stats_list = stats_list.copy()
        candidate_2_strengths = [candidate_2_stats_list.pop(random.randint(0, len(candidate_2_stats_list) - 1))]
        candidate_2_strengths.append(candidate_2_stats_list.pop(random.randint(0, len(candidate_2_stats_list) - 1)))
    
    candidate_2_stats_list_backup = candidate_2_stats_list.copy()
    candidate_2_weaknesses = [candidate_2_stats_list_backup.pop(random.randint(0, len(candidate_2_stats_list_backup) - 1))]
    candidate_2_weaknesses.append(candidate_2_stats_list_backup.pop(random.randint(0, len(candidate_2_stats_list_backup) - 1)))

    while candidate_2_weaknesses == candidate_1_weaknesses:
        candidate_2_stats_list_backup = candidate_2_stats_list.copy()
        candidate_2_weaknesses = [candidate_2_stats_list_backup.pop(random.randint(0, len(candidate_2_stats_list_backup) - 1))]
        candidate_2_weaknesses.append(candidate_2_stats_list_backup.pop(random.randint(0, len(candidate_2_stats_list_backup) - 1)))
    
    tier_1_positive_titles = []

    for title_key, title_details in json_data["titles"].items():
        if title_details["tier"] == 1 and title_details["type"] == "positive":
            tier_1_positive_titles.append(title_details["display_name"])

    candidate_1_title = random.choice(tier_1_positive_titles)
    candidate_2_title = random.choice(tier_1_positive_titles)
    while candidate_1_title == candidate_2_title:
        candidate_2_title = random.choice(tier_1_positive_titles)

    return render_template("elected_candidates_generator.html", candidate_1_strengths=candidate_1_strengths, candidate_1_weaknesses=candidate_1_weaknesses, candidate_2_strengths=candidate_2_strengths, candidate_2_weaknesses=candidate_2_weaknesses, candidate_1_title=candidate_1_title, candidate_2_title=candidate_2_title)

@admin_tool_routes.route("/karma_helper")
@admin_required
def karma_helper():
    schema, db = get_data_on_category("nations")

    player_nations = list(db.find({"temperament": "Player"}).sort("name", ASCENDING))
    ai_nations = list(db.find({"temperament": {"$ne": "Player"}}).sort("name", ASCENDING))

    for nation in player_nations + ai_nations:
        calculated_fields = calculate_all_fields(nation, schema,  "nation")
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
        calculated_fields = calculate_all_fields(nation, schema, "nation")
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