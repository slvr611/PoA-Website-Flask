from flask import Blueprint, render_template, redirect, request, flash, url_for, send_file
from helpers.auth_helpers import admin_required
from helpers.data_helpers import get_data_on_category, generate_id_to_name_dict, compute_demographics
from helpers.admin_tool_helpers import grow_all_population_async, roll_events_async
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

@admin_tool_routes.route("/roll_events")
@admin_required
def roll_events():
    message = roll_events_async()
    flash(message, "info")

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
    form_data = request.form.to_dict()
    message = grow_all_population_async(form_data)
    flash(message, "info")

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

@admin_tool_routes.route('/tick_summaries', methods=['GET'])
@admin_required
def admin_tick_summaries():
    """View available tick summaries"""
    summary_dir = os.path.join(os.getcwd(), 'summaries')
    os.makedirs(summary_dir, exist_ok=True)
    
    # Get all summary files
    summaries = []
    for filename in os.listdir(summary_dir):
        if filename.startswith('tick_summary_') and filename.endswith('.txt'):
            file_path = os.path.join(summary_dir, filename)
            file_stats = os.stat(file_path)
            
            # Extract timestamp from filename
            timestamp_str = filename.replace('tick_summary_', '').replace('.txt', '')
            try:
                timestamp = datetime.datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                formatted_date = timestamp.strftime('%Y-%m-%d %H:%M:%S')
            except:
                formatted_date = "Unknown"
            
            summaries.append({
                'filename': filename,
                'path': file_path,
                'size': file_stats.st_size,
                'date': formatted_date,
                'timestamp': timestamp_str
            })
    
    # Sort by date (newest first)
    summaries.sort(key=lambda x: x['timestamp'], reverse=True)
    
    return render_template('admin/tick_summaries.html', summaries=summaries)

@admin_tool_routes.route('/tick_summaries/<filename>', methods=['GET'])
@admin_required
def download_tick_summary(filename):
    """Download a specific tick summary"""
    summary_dir = os.path.join(os.getcwd(), 'summaries')
    file_path = os.path.join(summary_dir, filename)
    
    if not os.path.exists(file_path):
        flash("Summary file not found", "error")
        return redirect(url_for('admin_tick_summaries'))
    
    # Read file content
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Option to download or view
    if request.args.get('download') == 'true':
        return send_file(file_path, as_attachment=True)
    
    return render_template('admin/view_tick_summary.html', content=content, filename=filename)
