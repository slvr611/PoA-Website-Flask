from flask import Blueprint, render_template, redirect, request, flash, url_for
from helpers.auth_helpers import admin_required
from helpers.data_helpers import get_data_on_category, generate_id_to_name_dict, compute_demographics
from helpers.admin_tool_helpers import grow_population
from helpers.change_helpers import request_change, approve_change
from calculations.field_calculations import calculate_all_fields
from app_core import category_data, rarity_rankings, mongo, json_data
from pymongo import ASCENDING
from bson import ObjectId
from app_core import restore_mongodb
import random
import os
import datetime

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

        new_nation = nation.copy()
        new_nation.update({"temporary_karma": 0, "event_roll": event_roll, "raw_roll": raw_roll, "event_type": event_type,
                           "previous_karma": nation.get("karma", 0), "previous_temporary_karma": nation.get("temporary_karma", 0), "previous_rolling_karma": nation.get("rolling_karma", 0)})
        change_id = request_change(
            data_type="nations",
            item_id=nation["_id"],
            change_type="Update",
            before_data=nation,
            after_data=new_nation,
            reason="Karma Roll for " + nation["name"]
        )
        approve_change(change_id)
    
    return redirect("/karma_helper")

@admin_tool_routes.route("/pop_growth_helper")
@admin_required
def pop_growth_helper():
    schema, db = get_data_on_category("nations")
    nations = list(db.find().sort("name", ASCENDING))

    dropdown_options = {}
    for nation in nations:
        dropdown_options[nation["name"]] = nation["_id"]

    return render_template("pop_growth_helper.html", dropdown_options=dropdown_options)

@admin_tool_routes.route("/pop_growth_helper/process", methods=["POST"])
@admin_required
def process_pop_growth():
    schema, db = get_data_on_category("nations")
    nations = list(db.find().sort("name", ASCENDING))
    
    changes = []
    for nation in nations:
        nation_id = str(nation["_id"])
        include_key = f"include_{nation_id}"
        foreign_source_key = f"foreign_source_{nation_id}"
        
        # Check if this nation should be included in growth
        if include_key in request.form:
            foreign_nation_id = request.form.get(foreign_source_key)
            
            # Only process if a foreign nation was selected
            if foreign_nation_id:
                foreign_nation = db.find_one({"_id": ObjectId(foreign_nation_id)})
                if foreign_nation:
                    change_id = grow_population(nation, foreign_nation)
                    changes.append({
                        "nation": nation["name"],
                        "foreign_nation": foreign_nation["name"],
                        "change_id": change_id
                    })
    
    # Flash a message with the results
    if changes:
        change_messages = [f"{c['nation']} (from {c['foreign_nation']})" for c in changes]
        flash(f"Population growth processed for: {', '.join(change_messages)}")
    else:
        flash("No population growth processed. Please select nations and foreign sources.")
    
    return redirect("/pop_growth_helper")

@admin_tool_routes.route("/database_management", methods=["GET"])
@admin_required
def database_management():
    """Database backup and restore management page"""
    # Get list of available backups
    backups = []
    try:
        # S3 configuration
        s3_bucket = os.getenv("S3_BUCKET_NAME")
        aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        
        if s3_bucket and aws_access_key and aws_secret_key:
            # Create S3 client
            import boto3
            s3_client = boto3.client(
                's3',
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key
            )
            
            # List objects in the backups folder
            response = s3_client.list_objects_v2(
                Bucket=s3_bucket,
                Prefix='backups/'
            )
            
            if 'Contents' in response:
                for item in response['Contents']:
                    key = item['Key']
                    if key.endswith('.zip') and 'mongodb_backup_' in key:
                        # Extract filename and timestamp
                        filename = os.path.basename(key)
                        timestamp = filename.replace('mongodb_backup_', '').replace('.zip', '')
                        try:
                            date_obj = datetime.datetime.strptime(timestamp, '%Y%m%d_%H%M%S')
                            formatted_date = date_obj.strftime('%Y-%m-%d %H:%M:%S')
                            
                            backups.append({
                                'path': f"s3://{s3_bucket}/{key}",
                                'name': filename,
                                'timestamp': timestamp,
                                'date': formatted_date,
                                'is_zip': True,
                                'location': 's3',
                                's3_key': key,
                                's3_bucket': s3_bucket
                            })
                        except ValueError:
                            continue
    except Exception as e:
        flash(f"Error retrieving S3 backups: {str(e)}", "error")
    
    # Sort backups by timestamp (newest first)
    backups.sort(key=lambda x: x['timestamp'], reverse=True)
    
    return render_template("admin/database_management.html", backups=backups)

@admin_tool_routes.route("/backup_database", methods=["POST"])
@admin_required
def backup_database_route():
    """Create a database backup"""
    from app_core import backup_mongodb
    
    success, message = backup_mongodb()
    if success:
        flash(f"Database backup successful: {message}", "success")
    else:
        flash(f"Database backup failed: {message}", "error")
    
    return redirect(url_for("admin_tool_routes.database_management"))

@admin_tool_routes.route("/restore_database", methods=["POST"])
@admin_required
def restore_database_route():
    """Restore database from a backup"""    
    backup_path = request.form.get('backup_path')
    if not backup_path:
        flash("No backup selected for restoration", "error")
        return redirect(url_for("admin_tool_routes.database_management"))
    
    # Confirm restoration with a confirmation code
    confirmation_code = request.form.get('confirmation_code')
    expected_code = datetime.datetime.now().strftime('%Y%m%d')
    
    if confirmation_code != expected_code:
        flash("Invalid confirmation code. Database restoration aborted.", "error")
        return redirect(url_for("admin_tool_routes.database_management"))
    
    # Check if this is an S3 backup
    if backup_path.startswith('s3://'):
        # Parse S3 path
        s3_parts = backup_path.replace('s3://', '').split('/')
        s3_bucket = s3_parts[0]
        s3_key = '/'.join(s3_parts[1:])
        success, message = restore_mongodb(s3_key=s3_key, s3_bucket=s3_bucket)
    else:
        # Local backup
        success, message = restore_mongodb(backup_path=backup_path)
    if success:
        flash(f"Database restored successfully: {message}", "success")
    else:
        flash(f"Database restoration failed: {message}", "error")
    
    return redirect(url_for("admin_tool_routes.database_management"))