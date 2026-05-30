from flask import Blueprint, render_template, redirect, request, flash, url_for, send_file
from helpers.auth_helpers import admin_required
from helpers.data_helpers import get_data_on_category
from helpers.admin_tool_helpers import grow_all_population_async, roll_events_async, recalculate_all_items_async
from app_core import category_data, mongo, json_data, temperament_enum
from pymongo import ASCENDING
from app_core import restore_mongodb
from forms import form_generator
from io import BytesIO
from copy import deepcopy
import random
import os
import datetime
from bson import ObjectId

admin_tool_routes = Blueprint('admin_tool_routes', __name__)

@admin_tool_routes.route("/admin_tools")
@admin_required
def admin_tools():
    return render_template("admin_tools.html")

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

    for title_key, title_details in json_data["positive_titles"].items():
        if title_details["tier"] == 1:
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
    nations = list(db.find({}, {"name": 1, "_id": 1}).sort("name", ASCENDING))
    return render_template("pop_growth_helper.html", nations=nations)

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
    from app_core import backup_mongodb_async
    
    success, message = backup_mongodb_async()
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
    summaries = []
    s3_bucket = os.getenv("S3_BUCKET_NAME")
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    
    if s3_bucket and aws_access_key and aws_secret_key:
        try:
            import boto3
            s3_client = boto3.client(
                's3',
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key
            )
            
            response = s3_client.list_objects_v2(
                Bucket=s3_bucket,
                Prefix='tick_summaries/'
            )
            
            for item in response.get('Contents', []):
                key = item.get('Key', '')
                if key.endswith('/') or not key.endswith('.txt'):
                    continue
                
                filename = os.path.basename(key)
                if 'tick_summary_' not in filename:
                    continue
                
                timestamp_str = (
                    filename.replace('player_tick_summary_', '')
                           .replace('full_tick_summary_', '')
                           .replace('.txt', '')
                )
                try:
                    timestamp = datetime.datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                    formatted_date = timestamp.strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    formatted_date = "Unknown"
                
                summaries.append({
                    'filename': filename,
                    'path': key,
                    'size': item.get('Size', 0),
                    'date': formatted_date,
                    'timestamp': timestamp_str,
                    'location': 's3'
                })
        except Exception as e:
            flash(f"Error retrieving S3 tick summaries: {str(e)}", "error")
    
    # Fallback to local summaries if S3 is unavailable or empty
    if not summaries:
        summary_dir = os.path.join(os.getcwd(), 'summaries')
        os.makedirs(summary_dir, exist_ok=True)
        
        for filename in os.listdir(summary_dir):
            if filename.endswith('.txt') and 'tick_summary_' in filename:
                file_path = os.path.join(summary_dir, filename)
                file_stats = os.stat(file_path)
                
                timestamp_str = (
                    filename.replace('player_tick_summary_', '')
                           .replace('full_tick_summary_', '')
                           .replace('tick_summary_', '')
                           .replace('.txt', '')
                )
                try:
                    timestamp = datetime.datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                    formatted_date = timestamp.strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    formatted_date = "Unknown"
                
                summaries.append({
                    'filename': filename,
                    'path': file_path,
                    'size': file_stats.st_size,
                    'date': formatted_date,
                    'timestamp': timestamp_str,
                    'location': 'local'
                })
    
    # Sort by date (newest first)
    def summary_sort_key(summary):
        """Ensure entries with unknown timestamps are sorted last."""
        ts_str = summary.get('timestamp', '')
        try:
            ts = datetime.datetime.strptime(ts_str, '%Y%m%d_%H%M%S')
            return (1, ts)
        except Exception:
            return (0, datetime.datetime.min)

    summaries.sort(key=summary_sort_key, reverse=True)
    
    return render_template('admin/tick_summaries.html', summaries=summaries)

@admin_tool_routes.route('/tick_summaries/<filename>', methods=['GET'])
@admin_required
def download_tick_summary(filename):
    """Download a specific tick summary"""
    summary_dir = os.path.join(os.getcwd(), 'summaries')
    file_path = os.path.join(summary_dir, filename)
    
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            content = f.read()
        
        if request.args.get('download') == 'true':
            return send_file(file_path, as_attachment=True)
        
        return render_template('admin/view_tick_summary.html', content=content, filename=filename)
    
    s3_bucket = os.getenv("S3_BUCKET_NAME")
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    
    if s3_bucket and aws_access_key and aws_secret_key:
        try:
            import boto3
            s3_client = boto3.client(
                's3',
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key
            )
            
            s3_key = f"tick_summaries/{filename}"
            obj = s3_client.get_object(Bucket=s3_bucket, Key=s3_key)
            file_bytes = obj['Body'].read()
            content = file_bytes.decode('utf-8', errors='replace')
            
            if request.args.get('download') == 'true':
                file_stream = BytesIO(file_bytes)
                file_stream.seek(0)
                return send_file(file_stream, as_attachment=True, download_name=filename)
            
            return render_template('admin/view_tick_summary.html', content=content, filename=filename)
        except Exception as e:
            flash(f"Error retrieving summary from S3: {str(e)}", "error")
    else:
        flash("Summary file not found locally and S3 is not configured.", "error")
    
    return redirect(url_for('admin_tool_routes.admin_tick_summaries'))

@admin_tool_routes.route('/global_modifiers/item/global_modifiers')
@admin_required
def global_modifiers():
    global_modifiers = mongo.db.global_modifiers.find_one({"name": "global_modifiers"})
    if not global_modifiers:
        global_modifiers = {"name": "global_modifiers"}
        mongo.db.global_modifiers.insert_one(global_modifiers)
    
    return render_template('dataItem.html', 
                          item=global_modifiers, 
                          title="Global Modifier", 
                          category="global_modifiers",
                          schema=category_data["global_modifiers"]["schema"])

@admin_tool_routes.route('/global_modifiers/edit/global_modifiers', methods=['GET'])
@admin_required
def edit_global_modifiers():
    global_modifiers = mongo.db.global_modifiers.find_one({"name": "global_modifiers"})
    if not global_modifiers:
        global_modifiers = {"name": "global_modifiers"}
        mongo.db.global_modifiers.insert_one(global_modifiers)
    
    schema = category_data["global_modifiers"]["schema"]
    form = form_generator.get_form("global_modifiers", schema, item=global_modifiers)
    
    return render_template('dataItemEdit.html', 
                          item=global_modifiers, 
                          title="Global Modifier", 
                          category="global_modifiers",
                          schema=schema,
                          form=form)

@admin_tool_routes.route("/temperament_overview")
@admin_required
def temperament_overview():
    schema, db = get_data_on_category("nations")
    nations = list(db.find().sort("name", ASCENDING))
    
    # Group nations by temperament
    temperament_groups = {}
    for temperament in temperament_enum:
        temperament_groups[temperament] = []
    
    for nation in nations:
        temperament = nation.get("temperament", "Neutral")
        if temperament not in temperament_groups:
            temperament_groups[temperament] = []
        temperament_groups[temperament].append(nation)
    
    # Count nations per temperament
    temperament_counts = {temp: len(nations) for temp, nations in temperament_groups.items()}
    
    return render_template("temperament_overview.html", 
                         temperament_groups=temperament_groups,
                         temperament_counts=temperament_counts,
                         temperament_enum=temperament_enum)

@admin_tool_routes.route("/player_law_analysis")
@admin_required
def player_law_analysis():
    schema, db = get_data_on_category("nations")
    player_nations = list(db.find({"temperament": "Player"}).sort("name", ASCENDING))
    
    if not player_nations:
        return render_template("player_law_analysis.html", 
                             law_stats={}, 
                             total_players=0)
    
    total_players = len(player_nations)
    law_stats = {}
    
    # Get all law fields from schema
    law_fields = []
    for field_name, field_data in schema["properties"].items():
        if field_data.get("bsonType") == "enum" and "laws" in field_data:
            law_fields.append(field_name)
    
    # Calculate percentages for each law field
    for law_field in law_fields:
        law_stats[law_field] = {
            "label": schema["properties"][law_field].get("label", law_field),
            "options": {}
        }
        
        # Count occurrences of each option
        for option in schema["properties"][law_field]["enum"]:
            count = sum(1 for nation in player_nations if nation.get(law_field) == option)
            percentage = (count / total_players) * 100 if total_players > 0 else 0
            law_stats[law_field]["options"][option] = {
                "count": count,
                "percentage": round(percentage, 1)
            }
    
    return render_template("player_law_analysis.html", 
                         law_stats=law_stats, 
                         total_players=total_players)

@admin_tool_routes.route("/player_district_analysis")
@admin_required
def player_district_analysis():
    schema, db = get_data_on_category("nations")
    player_nations = list(db.find({"temperament": "Player"}).sort("name", ASCENDING))

    if not player_nations:
        return render_template(
            "player_district_analysis.html",
            district_stats={},
            total_players=0,
            total_districts=0,
        )

    def synergy_matches(node, requirement):
        if not node:
            return False
        if isinstance(requirement, list):
            return "any" in requirement or node in requirement
        return requirement == "any" or node == requirement

    def get_synergies(dd):
        if "synergies" in dd:
            return dd["synergies"]
        req = dd.get("synergy_requirement", "")
        mods = dd.get("synergy_modifiers", {})
        if req or mods:
            return [{"requirement": req}]
        return []

    district_stats = {}
    category_stats = {}
    total_districts = 0
    imperial_data = json_data["nation_imperial_districts"]

    for nation in player_nations:
        if nation.get("empire", False):
            imperial = nation.get("imperial_district", {})
            imperial_type = imperial.get("type", "")
            imperial_node = imperial.get("node", "")
            if imperial_type:
                imperial_dd = imperial_data.get(imperial_type, {})
                synergy_active = any(synergy_matches(imperial_node, syn.get("requirement", "")) for syn in get_synergies(imperial_dd))
                key = f"Imperial: {imperial_type}"
                label = imperial_data.get(imperial_type, {}).get("name", key)
                stats = district_stats.setdefault(
                    key,
                    {"label": label, "active": 0, "inactive": 0},
                )
                if synergy_active:
                    stats["active"] += 1
                else:
                    stats["inactive"] += 1
                total_districts += 1

                category_label = label
                tier_label = ""
                for prefix, tier in (("Ancient ", "Ancient"), ("Classical ", "Classical"), ("ancient_", "Ancient"), ("classical_", "Classical")):
                    if category_label.startswith(prefix):
                        category_label = category_label[len(prefix):]
                        tier_label = tier
                        break
                category_label = category_label.lower()
                category_entry = category_stats.setdefault(
                    category_label,
                    {
                        "label": category_label,
                        "active": 0,
                        "inactive": 0,
                        "ancient": 0,
                        "classical": 0,
                    },
                )
                if synergy_active:
                    category_entry["active"] += 1
                else:
                    category_entry["inactive"] += 1
                if tier_label == "Ancient":
                    category_entry["ancient"] += 1
                elif tier_label == "Classical":
                    category_entry["classical"] += 1

    for stats in district_stats.values():
        total = stats["active"] + stats["inactive"]
        stats["total"] = total
        stats["active_pct"] = round((stats["active"] / total) * 100, 1) if total else 0
    for stats in category_stats.values():
        total = stats["active"] + stats["inactive"]
        stats["total"] = total
        stats["active_pct"] = round((stats["active"] / total) * 100, 1) if total else 0
        stats["ancient_pct"] = round((stats["ancient"] / total) * 100, 1) if total else 0
        stats["classical_pct"] = round((stats["classical"] / total) * 100, 1) if total else 0

    category_list = sorted(
        category_stats.values(),
        key=lambda item: (item.get("total", 0), item.get("label", "")),
        reverse=True,
    )
    district_list = sorted(
        district_stats.values(),
        key=lambda item: (item.get("total", 0), item.get("label", "")),
        reverse=True,
    )

    return render_template(
        "player_district_analysis.html",
        district_stats=district_stats,
        category_stats=category_stats,
        category_list=category_list,
        district_list=district_list,
        total_players=len(player_nations),
        total_districts=total_districts,
    )

@admin_tool_routes.route("/recalculate_all_objects")
@admin_required
def recalculate_all_objects_route():
    recalculate_all_items_async()
    flash("Recalculation process started in background. Check logs for results.", "info")
    return redirect("/")


def _suffix_to_num(suffix):
    n = 0
    for ch in suffix:
        n = n * 26 + (ord(ch) - ord('A') + 1)
    return n


def _num_to_suffix(n):
    result = ""
    while n > 0:
        n -= 1
        result = chr(ord('A') + n % 26) + result
        n //= 26
    return result


@admin_tool_routes.route("/placeholder_nations", methods=["GET"])
@admin_required
def placeholder_nations():
    existing = list(mongo.db.nations.find(
        {"name": {"$regex": "^Placeholder [A-Z]+$"}},
        {"name": 1, "_id": 1}
    ).sort("name", ASCENDING))
    return render_template("placeholder_nations.html", existing=existing)


@admin_tool_routes.route("/placeholder_nations/create", methods=["POST"])
@admin_required
def create_placeholder_nations():
    try:
        count = int(request.form.get("count", 1))
    except (ValueError, TypeError):
        count = 1
    count = max(1, min(count, 500))

    existing_names = {
        n["name"] for n in mongo.db.nations.find(
            {"name": {"$regex": "^Placeholder [A-Z]+$"}},
            {"name": 1}
        )
    }

    max_num = 0
    for name in existing_names:
        suffix = name[len("Placeholder "):]
        if suffix.isalpha() and suffix.isupper():
            max_num = max(max_num, _suffix_to_num(suffix))

    to_insert = []
    n = max_num + 1
    while len(to_insert) < count:
        suffix = _num_to_suffix(n)
        name = f"Placeholder {suffix}"
        if name not in existing_names:
            to_insert.append({
                "name": name,
                "temperament": "Neutral",
                "government_type": "Ruthless Meritocracy",
                "succession_type": "Inherited",
                "foreign_acceptance": "Acceptance",
                "origin": "Unknown",
                "sessions_since_temperament_change": 1,
                "money": 0,
                "infamy": 0,
                "rolling_karma": 0,
                "temporary_karma": 0,
                "road_usage": 0,
                "storage": {},
                "districts": [],
                "cities": [],
                "jobs": [],
                "technologies": [],
            })
        n += 1

    if to_insert:
        mongo.db.nations.insert_many(to_insert)

    flash(f"Created {len(to_insert)} placeholder nation(s).", "success")
    return redirect(url_for("admin_tool_routes.placeholder_nations"))


@admin_tool_routes.route("/placeholder_nations/delete", methods=["POST"])
@admin_required
def delete_placeholder_nations():
    result = mongo.db.nations.delete_many({"name": {"$regex": "^Placeholder [A-Z]+$"}})
    flash(f"Deleted {result.deleted_count} placeholder nation(s).", "success")
    return redirect(url_for("admin_tool_routes.placeholder_nations"))


# ---------------------------------------------------------------------------
# District / Node wipe helpers
# ---------------------------------------------------------------------------

@admin_tool_routes.route("/admin/wipe_districts", methods=["POST"])
@admin_required
def wipe_all_districts():
    """Clear the districts array on every nation. Imperial district is left intact."""
    result = mongo.db.nations.update_many(
        {"districts": {"$exists": True, "$ne": []}},
        {"$set": {"districts": []}}
    )
    flash(
        f"Wiped districts from {result.modified_count} nation(s). "
        "Imperial districts were not affected.",
        "success"
    )
    return redirect(url_for("admin_tool_routes.admin_tools"))


@admin_tool_routes.route("/admin/wipe_nodes", methods=["POST"])
@admin_required
def wipe_all_nodes():
    """Remove the node field from every district and imperial district, and
    clear the nodes array from every city, across all nations."""
    # Unset node from every element of districts[] and from imperial_district
    mongo.db.nations.update_many(
        {},
        {"$unset": {"districts.$[].node": "", "imperial_district.node": ""}}
    )
    # Clear nodes array from every element of cities[]
    mongo.db.nations.update_many(
        {"cities": {"$exists": True, "$ne": []}},
        {"$set": {"cities.$[].nodes": []}}
    )
    flash("Wiped all district nodes and city nodes across all nations.", "success")
    return redirect(url_for("admin_tool_routes.admin_tools"))


# ---------------------------------------------------------------------------
# Visibility bypass log viewer
# ---------------------------------------------------------------------------

@admin_tool_routes.route("/admin/visibility_log")
@admin_required
def visibility_log():
    PAGE_SIZE = 50

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    admin_filter  = request.args.get("admin", "").strip()
    source_filter = request.args.get("source", "all")  # "all" | "nation" | "map"

    query = {}
    if admin_filter:
        query["admin_username"] = {"$regex": admin_filter, "$options": "i"}
    if source_filter == "nation":
        query["nation"] = {"$exists": True}
    elif source_filter == "map":
        query["action"] = "map_admin_view_enabled"

    total  = mongo.db.admin_visibility_logs.count_documents(query)
    skip   = (page - 1) * PAGE_SIZE
    entries = list(
        mongo.db.admin_visibility_logs
        .find(query)
        .sort("timestamp", -1)
        .skip(skip)
        .limit(PAGE_SIZE)
    )

    # Coerce ObjectId to string so Jinja can render it
    for e in entries:
        e["_id"] = str(e["_id"])

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    return render_template(
        "admin/visibility_log.html",
        entries=entries,
        page=page,
        total_pages=total_pages,
        total=total,
        admin_filter=admin_filter,
        source_filter=source_filter,
    )


# ---------------------------------------------------------------------------
# AI Market Matching — mid-session manual trigger
# ---------------------------------------------------------------------------

@admin_tool_routes.route("/run_ai_market_matching", methods=["POST"])
@admin_required
def run_ai_market_matching():
    """Run the AI-to-AI market order matching tick immediately against the live DB."""
    from helpers.ai_decision_helpers import ai_market_matching_tick
    from helpers.change_helpers import system_request_change, system_approve_change
    from calculations.field_calculations import calculate_all_fields
    from copy import deepcopy

    nation_schema, nation_db = get_data_on_category("nations")
    old_nations = list(nation_db.find().sort("name", ASCENDING))
    new_nations = []
    for nation in old_nations:
        if nation:
            nation.update(calculate_all_fields(nation, nation_schema, "nation"))
            new_nations.append(deepcopy(nation))

    result = ai_market_matching_tick(old_nations, new_nations, nation_schema)

    for i in range(len(old_nations)):
        change_id = system_request_change(
            data_type="nations",
            item_id=old_nations[i]["_id"],
            change_type="Update",
            before_data=old_nations[i],
            after_data=new_nations[i],
            reason="AI Market Matching (manual trigger)"
        )
        system_approve_change(change_id)

    flash(f"AI market matching complete. {result}", "success")
    return redirect(url_for("admin_tool_routes.admin_tools"))


# ---------------------------------------------------------------------------
# AI Personality Editor
# ---------------------------------------------------------------------------

_AI_PERSONALITY_DIMS = [
    ("aggression", "Aggression", "Likelihood to declare war or take hostile actions (-1 = pacifist, +1 = warmonger)"),
    ("military",   "Military",   "Priority given to military units and wonders (-1 = minimal, +1 = heavily armed)"),
    ("economic",   "Economic",   "Drive to build economic districts and stockpile resources (-1 = subsistence, +1 = growth-focused)"),
    ("expansion",  "Expansion",  "Desire to claim new territory (-1 = static, +1 = expansionist)"),
    ("trade",      "Trade",      "Willingness to engage in market trades (-1 = autarkic, +1 = trade-focused)"),
]


@admin_tool_routes.route("/ai_personality", methods=["GET"])
@admin_required
def ai_personality_list():
    """List all AI nations for personality editing."""
    schema, db = get_data_on_category("nations")
    ai_nations = list(db.find({"temperament": {"$ne": "Player"}}, {"name": 1, "temperament": 1, "ai_personality": 1}).sort("name", ASCENDING))
    return render_template("admin/ai_personality_list.html", nations=ai_nations, dims=_AI_PERSONALITY_DIMS)


@admin_tool_routes.route("/ai_personality/<nation_id>", methods=["GET", "POST"])
@admin_required
def edit_ai_personality(nation_id):
    """Edit the ai_personality overrides for a single nation."""
    from helpers.change_helpers import system_request_change, system_approve_change

    try:
        nation = mongo.db.nations.find_one({"_id": ObjectId(nation_id)})
    except Exception:
        flash("Nation not found.", "error")
        return redirect(url_for("admin_tool_routes.ai_personality_list"))

    if not nation:
        flash("Nation not found.", "error")
        return redirect(url_for("admin_tool_routes.ai_personality_list"))

    if request.method == "POST":
        new_personality = {}
        for key, _label, _desc in _AI_PERSONALITY_DIMS:
            raw = request.form.get(key, "0")
            try:
                val = max(-1.0, min(1.0, float(raw)))
            except (ValueError, TypeError):
                val = 0.0
            new_personality[key] = val

        old_nation = deepcopy(nation)
        new_nation = deepcopy(nation)
        new_nation["ai_personality"] = new_personality

        change_id = system_request_change(
            data_type="nations",
            item_id=nation["_id"],
            change_type="Update",
            before_data=old_nation,
            after_data=new_nation,
            reason=f"AI Personality edited via admin tool"
        )
        system_approve_change(change_id)
        flash(f"AI personality updated for {nation.get('name', nation_id)}.", "success")
        return redirect(url_for("admin_tool_routes.ai_personality_list"))

    current = nation.get("ai_personality", {})
    return render_template(
        "admin/ai_personality_edit.html",
        nation=nation,
        dims=_AI_PERSONALITY_DIMS,
        current=current,
    )
