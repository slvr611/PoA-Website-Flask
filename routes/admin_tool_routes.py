from flask import Blueprint, render_template, redirect, request, flash, url_for, send_file, jsonify
from helpers.auth_helpers import admin_required
from helpers.data_helpers import get_data_on_category
from helpers.admin_tool_helpers import grow_all_population_async, roll_events_async, recalculate_all_items_async
from app_core import category_data, mongo, json_data, temperament_enum, find_dict_in_list
from helpers.data_item_view_helpers import build_item_view
from pymongo import ASCENDING
from app_core import restore_mongodb_async
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

    _karma_fields = {
        "_id": 0, "name": 1,
        "event_type": 1, "event_roll": 1, "raw_roll": 1,
        "previous_karma": 1, "previous_rolling_karma": 1, "previous_temporary_karma": 1,
        "karma": 1, "rolling_karma": 1, "temporary_karma": 1,
    }
    player_nations = list(db.find({"temperament": "Player"}, _karma_fields).sort("name", ASCENDING))
    ai_nations = list(db.find({"temperament": {"$ne": "Player"}}, _karma_fields).sort("name", ASCENDING))

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

# ── Civil War Helper ─────────────────────────────────────────────────────────

# Fields never copied to the breakaway nation: identity, relationships,
# map-derived data, and cached/calculated values (recomputed on approval).
_CIVIL_WAR_EXCLUDED_FIELDS = [
    "_id", "name",
    "rulers", "players",
    "wars", "factions", "hired_mercenaries",
    "markets", "owned_markets",
    "overlord", "vassal_type", "vassals", "compliance", "concessions",
    "diplomatic_relations_1", "diplomatic_relations_2",
    "pops",
    "territory_types", "road_usage",
    "imperial_district",
    "progress_quests",
    "ai_state", "notes",
    "breakdowns", "visibility_modifiers",
    "pending_civil_war",
]


def _civil_war_pop_groups(nation_id_str):
    """Group a nation's pops by race/culture/religion/slave with display names."""
    pipeline = [
        {"$match": {"nation": nation_id_str}},
        {"$group": {
            "_id": {
                "race":     {"$ifNull": ["$race", ""]},
                "culture":  {"$ifNull": ["$culture", ""]},
                "religion": {"$ifNull": ["$religion", ""]},
                "slave":    {"$ifNull": ["$slave", False]},
            },
            "count": {"$sum": 1},
        }},
    ]
    groups = list(mongo.db.pops.aggregate(pipeline))

    def _names(collection, ids):
        ids = [i for i in ids if i]
        if not ids:
            return {}
        docs = collection.find(
            {"_id": {"$in": [ObjectId(i) for i in ids]}}, {"name": 1}
        )
        return {str(d["_id"]): d.get("name", "?") for d in docs}

    race_names     = _names(mongo.db.races,     {g["_id"]["race"] for g in groups})
    culture_names  = _names(mongo.db.cultures,  {g["_id"]["culture"] for g in groups})
    religion_names = _names(mongo.db.religions, {g["_id"]["religion"] for g in groups})

    result = []
    for g in groups:
        k = g["_id"]
        slave_flag = "1" if k["slave"] else "0"
        result.append({
            "key":      f"{k['race']}|{k['culture']}|{k['religion']}|{slave_flag}",
            "race":     race_names.get(k["race"], "None"),
            "culture":  culture_names.get(k["culture"], "None"),
            "religion": religion_names.get(k["religion"], "None"),
            "slave":    bool(k["slave"]),
            "count":    g["count"],
        })
    result.sort(key=lambda x: (x["slave"], x["race"], x["culture"], x["religion"]))
    return result


@admin_tool_routes.route("/civil_war_helper")
@admin_required
def civil_war_helper():
    schema, db = get_data_on_category("nations")
    nations = list(db.find({}, {"name": 1}).sort("name", ASCENDING))
    flagged_nations = list(db.find({"pending_civil_war": True}, {"name": 1, "infamy": 1}).sort("name", ASCENDING))

    selected_nation = None
    pop_groups = []
    selected_id = request.args.get("nation", "")
    if selected_id:
        try:
            selected_nation = db.find_one({"_id": ObjectId(selected_id)}, {"name": 1})
        except Exception:
            selected_nation = None
        if selected_nation:
            pop_groups = _civil_war_pop_groups(selected_id)

    return render_template(
        "civil_war_helper.html",
        nations=nations,
        flagged_nations=flagged_nations,
        selected_nation=selected_nation,
        selected_id=selected_id,
        pop_groups=pop_groups,
    )


@admin_tool_routes.route("/civil_war_helper/dismiss_flag", methods=["POST"])
@admin_required
def civil_war_dismiss_flag():
    schema, db = get_data_on_category("nations")
    nation_id = request.form.get("nation_id", "")
    try:
        result = db.update_one({"_id": ObjectId(nation_id)}, {"$set": {"pending_civil_war": False}})
    except Exception:
        result = None
    if not result or result.matched_count == 0:
        flash("Nation not found.")
    else:
        flash("Pending civil war flag dismissed.")
    return redirect("/civil_war_helper")


@admin_tool_routes.route("/civil_war_helper/execute", methods=["POST"])
@admin_required
def civil_war_execute():
    from helpers.change_helpers import request_change, approve_change, _calculate_and_attach_fields

    schema, db = get_data_on_category("nations")

    source_id = request.form.get("source_nation", "")
    new_name = (request.form.get("new_name") or "").strip()

    try:
        source = db.find_one({"_id": ObjectId(source_id)})
    except Exception:
        source = None
    if source is None:
        flash("Source nation not found.")
        return redirect("/civil_war_helper")
    if not new_name:
        flash("The new nation needs a name.")
        return redirect(f"/civil_war_helper?nation={source_id}")
    from helpers.form_helpers import validate_name_characters
    name_valid, name_error = validate_name_characters(new_name)
    if not name_valid:
        flash(name_error)
        return redirect(f"/civil_war_helper?nation={source_id}")
    if db.find_one({"name": new_name}) is not None:
        flash(f"A nation named '{new_name}' already exists.")
        return redirect(f"/civil_war_helper?nation={source_id}")

    # ── Build the breakaway nation as a copy of the source ──
    new_nation = deepcopy(source)
    for f in _CIVIL_WAR_EXCLUDED_FIELDS:
        new_nation.pop(f, None)
    if request.form.get("copy_units") != "on":
        for f in ["land_units", "naval_units", "support_units"]:
            new_nation.pop(f, None)
    if request.form.get("copy_districts") != "on":
        for f in ["districts", "cities", "wonders"]:
            new_nation.pop(f, None)
    new_nation["name"] = new_name

    # Transfer a percentage of money and stored resources (subtracted from source)
    try:
        pct = max(0, min(100, int(request.form.get("transfer_pct") or 0))) / 100.0
    except ValueError:
        pct = 0.0
    money_moved = int(source.get("money", 0) * pct)
    storage_moved = {}
    for res, qty in (source.get("resource_storage") or {}).items():
        moved = int((qty or 0) * pct)
        if moved:
            storage_moved[res] = moved
    new_nation["money"] = money_moved
    new_nation["resource_storage"] = storage_moved

    # Create via the change system for an auditable, revertible record
    change_id = request_change(
        data_type="nations",
        item_id=None,
        change_type="Add",
        before_data={},
        after_data=new_nation,
        reason=f"Civil war: split from {source['name']}",
    )
    approve_change(change_id)

    new_doc = db.find_one({"name": new_name}, {"_id": 1})
    if new_doc is None:
        flash("Failed to create the new nation — see the change log.")
        return redirect(f"/civil_war_helper?nation={source_id}")
    new_id_str = str(new_doc["_id"])

    # ── Subtract transferred money/resources from the source, and clear the
    #    guaranteed-civil-war flag now that it has been resolved ──
    updates = {"pending_civil_war": False}
    if money_moved or storage_moved:
        updates["money"] = source.get("money", 0) - money_moved
        for res, moved in storage_moved.items():
            updates[f"resource_storage.{res}"] = (source.get("resource_storage", {}).get(res, 0)) - moved
    db.update_one({"_id": source["_id"]}, {"$set": updates})

    # ── Move the selected pops ──
    moved_pops = 0
    for key, val in request.form.items():
        if not key.startswith("move|"):
            continue
        try:
            qty = int(val or 0)
        except ValueError:
            qty = 0
        if qty <= 0:
            continue
        _, race, culture, religion, slave_flag = key.split("|")
        # Empty group values must also match pops where the field is missing/null
        def _field_match(v):
            return v if v else {"$in": ["", None]}
        match = {
            "nation": source_id,
            "race": _field_match(race),
            "culture": _field_match(culture),
            "religion": _field_match(religion),
        }
        if slave_flag == "1":
            match["slave"] = True
        else:
            match["slave"] = {"$ne": True}
        ids = [d["_id"] for d in mongo.db.pops.find(match, {"_id": 1}).limit(qty)]
        if ids:
            mongo.db.pops.update_many({"_id": {"$in": ids}}, {"$set": {"nation": new_id_str}})
            moved_pops += len(ids)

    # ── Recalculate both nations so pop counts and derived fields are current ──
    for nid in [source["_id"], new_doc["_id"]]:
        doc = db.find_one({"_id": nid})
        if doc:
            doc = _calculate_and_attach_fields("nations", doc)
            db.update_one({"_id": nid}, {"$set": {k: v for k, v in doc.items() if k != "_id"}})

    flash(
        f"Civil war executed: created '{new_name}' (change #{change_id}), moved {moved_pops} pops"
        + (f", transferred {int(pct * 100)}% of money and stored resources" if pct > 0 else "")
        + ". Now paint the new nation's territory on the map, then run Sync Territory."
    )
    return redirect(f"/nations/item/{new_name}")


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
        success, message = restore_mongodb_async(s3_key=s3_key, s3_bucket=s3_bucket)
    else:
        # Local backup
        success, message = restore_mongodb_async(backup_path=backup_path)
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
    
    schema = category_data["global_modifiers"]["schema"]
    item_view = build_item_view(
        schema, global_modifiers, {}, {}, json_data,
        None, None, False, 999,
        find_dict_in_list, json_data.get("scope_definitions", {}),
    )
    return render_template('dataItem.html',
                          item=global_modifiers,
                          title="Global Modifier",
                          category="global_modifiers",
                          schema=schema,
                          field_tiers=None,
                          visibility_level=None,
                          visibility_bypassed=False,
                          json_data=json_data,
                          item_view=item_view)

@admin_tool_routes.route('/global_modifiers/edit/global_modifiers', methods=['GET'])
@admin_required
def edit_global_modifiers():
    global_modifiers = mongo.db.global_modifiers.find_one({"name": "global_modifiers"})
    if not global_modifiers:
        global_modifiers = {"name": "global_modifiers"}
        mongo.db.global_modifiers.insert_one(global_modifiers)
    
    schema = category_data["global_modifiers"]["schema"]
    form = form_generator.get_form("global_modifiers", schema, item=global_modifiers)
    
    return render_template('dataItem.html',
                          item=global_modifiers,
                          title="Global Modifier",
                          category="global_modifiers",
                          schema=schema,
                          form=form,
                          data_type="global_modifiers",
                          entity_source_type="global",
                          editable=True)

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
    # Unset node from every element of districts[] (only on docs where the array exists)
    mongo.db.nations.update_many(
        {"districts": {"$exists": True, "$not": {"$size": 0}}},
        {"$unset": {"districts.$[].node": ""}}
    )
    # Unset node from imperial_district (only on docs where the field exists)
    mongo.db.nations.update_many(
        {"imperial_district.node": {"$exists": True}},
        {"$unset": {"imperial_district.node": ""}}
    )
    # Clear nodes array from every element of cities[]
    mongo.db.nations.update_many(
        {"cities": {"$exists": True, "$not": {"$size": 0}}},
        {"$set": {"cities.$[].nodes": []}}
    )
    flash("Wiped all district nodes and city nodes across all nations.", "success")
    return redirect(url_for("admin_tool_routes.admin_tools"))


# ---------------------------------------------------------------------------
# Wipe AI resource desires
# ---------------------------------------------------------------------------

@admin_tool_routes.route("/admin/approve_system_changes", methods=["POST"])
@admin_required
def approve_system_changes():
    """Force-approve all pending changes with 'System' as requester."""
    from helpers.change_helpers import system_force_approve_change
    pending = list(mongo.db.changes.find(
        {"status": "Pending", "requester.name": "System"},
        {"_id": 1}
    ))
    approved = 0
    failed = 0
    for change in pending:
        try:
            if system_force_approve_change(change["_id"]):
                approved += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    msg = f"Force-approved {approved} system change(s)."
    if failed:
        msg += f" {failed} failed."
    flash(msg, "success" if failed == 0 else "warning")
    return redirect(url_for("admin_tool_routes.admin_tools"))


@admin_tool_routes.route("/admin/wipe_ai_desires", methods=["POST"])
@admin_required
def wipe_ai_desires():
    """Clear resource_desires on all non-player nations."""
    player_ids = _get_player_nation_ids()
    result = mongo.db.nations.update_many(
        {"_id": {"$nin": list(player_ids)}, "resource_desires": {"$exists": True, "$ne": []}},
        {"$set": {"resource_desires": []}}
    )
    flash(f"Cleared resource desires from {result.modified_count} non-player nation(s).", "success")
    return redirect(url_for("admin_tool_routes.admin_tools"))


def _all_tiles_by_owner():
    """Batch-fetch every tile in a single query, grouped by owner, with the
    full projection _compute_legal_placement needs. Avoids two separate
    hex_map_tiles queries per nation (one for city matching, one for legal
    placement) when syncing all nations at once — that N+1 pattern was slow
    enough to time out in production with a larger nation count."""
    by_owner = {}
    for t in mongo.db.hex_map_tiles.find(
        {"owner": {"$nin": [None, ""]}},
        {"q": 1, "r": 1, "terrain": 1, "district": 1, "city": 1, "wonder": 1,
         "capital": 1, "node": 1, "owner": 1},
    ):
        by_owner.setdefault(t.get("owner", ""), []).append(t)
    return by_owner


@admin_tool_routes.route("/admin/sync_cities", methods=["GET"])
@admin_required
def sync_cities_preview():
    """Read-only preview of city sync between the map and AI nation pages."""
    from helpers.ai_decision_helpers import sync_nation_cities

    player_ids = _get_player_nation_ids()
    ai_nations = list(mongo.db.nations.find({"_id": {"$nin": list(player_ids)}}).sort("name", ASCENDING))
    tiles_by_owner = _all_tiles_by_owner()

    reports = []
    for n in ai_nations:
        owned = tiles_by_owner.get(n.get("name", ""), [])
        report = sync_nation_cities(n, dry_run=True, tiles_with_city=owned, owned_tiles=owned)
        if report["added_to_nation"] or report["placed_on_map"] or report["unplaceable"]:
            reports.append(report)

    return render_template("sync_cities.html", reports=reports)


@admin_tool_routes.route("/admin/sync_cities/apply", methods=["POST"])
@admin_required
def sync_cities_apply():
    """Apply the city sync between the map and AI nation pages for all non-player nations."""
    from helpers.ai_decision_helpers import sync_nation_cities
    from helpers.hex_map_helpers import bump_tile_version

    player_ids = _get_player_nation_ids()
    ai_nations = list(mongo.db.nations.find({"_id": {"$nin": list(player_ids)}}))
    tiles_by_owner = _all_tiles_by_owner()

    total_add = total_place = total_unplaceable = 0
    for n in ai_nations:
        owned = tiles_by_owner.get(n.get("name", ""), [])
        report = sync_nation_cities(n, dry_run=False, tiles_with_city=owned, owned_tiles=owned)
        total_add += len(report["added_to_nation"])
        total_place += len(report["placed_on_map"])
        total_unplaceable += len(report["unplaceable"])

    if total_place:
        bump_tile_version()

    msg = f"Synced cities: {total_add} added to nation pages, {total_place} placed on the map."
    if total_unplaceable:
        msg += f" {total_unplaceable} could not be placed (no legal tile)."
    flash(msg, "success")
    return redirect(url_for("admin_tool_routes.admin_tools"))


# ---------------------------------------------------------------------------
# Wipe concessions
# ---------------------------------------------------------------------------

@admin_tool_routes.route("/admin/wipe_concessions", methods=["POST"])
@admin_required
def wipe_concessions():
    """Clear the concessions dict on every nation, paying the demanded
    resources into each nation's stockpile (clamped to resource capacity)."""
    nations = list(mongo.db.nations.find(
        {"concessions": {"$exists": True, "$ne": {}}},
        {"concessions": 1, "resource_storage": 1, "nation_resource_capacity": 1},
    ))
    for nation in nations:
        concessions = nation.get("concessions")
        if not isinstance(concessions, dict):
            concessions = {}
        storage = dict(nation.get("resource_storage") or {})
        caps = nation.get("nation_resource_capacity") or {}
        for res, qty in concessions.items():
            if not isinstance(qty, (int, float)) or qty <= 0:
                continue
            new_amount = storage.get(res, 0) + qty
            cap = caps.get(res)
            if cap is not None:
                new_amount = min(new_amount, cap)
            storage[res] = new_amount
        mongo.db.nations.update_one(
            {"_id": nation["_id"]},
            {"$set": {"concessions": {}, "resource_storage": storage}},
        )
    flash(
        f"Wiped concessions from {len(nations)} nation(s) and added the demanded "
        f"resources to their stockpiles.",
        "success",
    )
    return redirect(url_for("admin_tool_routes.admin_tools"))


# ---------------------------------------------------------------------------
# Delete units by era
# ---------------------------------------------------------------------------

@admin_tool_routes.route("/admin/delete_units_by_era", methods=["POST"])
@admin_required
def delete_units_by_era():
    era = request.form.get("era", "").strip()
    if not era:
        flash("No era specified.", "danger")
        return redirect(url_for("admin_tool_routes.admin_tools"))
    result = mongo.db.units.delete_many({"era": era})
    flash(f"Deleted {result.deleted_count} unit(s) from the {era} era.", "success")
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
    """Start AI market matching in a background thread.

    Returns immediately — the matching loads its own nation data, runs all
    Dijkstra distance checks, executes trades, and commits only the changed
    nations to the DB.  Progress is printed to the server log.
    """
    from helpers.tick_helpers import run_ai_market_matching_async
    message = run_ai_market_matching_async()
    flash(message, "info")
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


# ---------------------------------------------------------------------------
# Randomize AI Laws
# ---------------------------------------------------------------------------

def _load_law_weights():
    import json as _json
    weights_path = os.path.join(os.path.dirname(__file__), '..', 'json-data', 'law_randomization_weights.json')
    with open(os.path.normpath(weights_path), 'r', encoding='utf-8') as f:
        return _json.load(f)


def _get_player_nation_ids():
    """Return a set of nation _id values (as ObjectIds) that are assigned to a player."""
    player_nation_ids = set()
    for char in mongo.db.characters.find(
        {"player": {"$exists": True, "$ne": None, "$ne": ""},
         "ruling_nation_org": {"$exists": True, "$ne": None}},
        {"ruling_nation_org": 1, "_id": 0},
    ):
        rno = char.get("ruling_nation_org")
        if rno:
            try:
                player_nation_ids.add(ObjectId(str(rno)))
            except Exception:
                pass
    for nation in mongo.db.nations.find(
        {"players": {"$exists": True, "$ne": [], "$ne": None}},
        {"_id": 1},
    ):
        player_nation_ids.add(nation["_id"])
    return player_nation_ids


def _pick_law(options, law_key, culture_traits, weights_data):
    """Return a weighted-random choice from options using base + trait weights."""
    base = weights_data.get("base_weights", {}).get(law_key, {})
    trait_weights_map = weights_data.get("trait_weights", {})
    weights = []
    for option in options:
        w = float(base.get(option, 1.0))
        for trait in culture_traits:
            if trait and trait != "None":
                tw = float(trait_weights_map.get(trait, {}).get(law_key, {}).get(option, 1.0))
                w *= tw
        weights.append(max(w, 0.0))
    total = sum(weights)
    if total == 0:
        return random.choice(options)
    return random.choices(options, weights=weights, k=1)[0]


def _randomize_nation_laws(nation, schema, weights_data, cultures_by_id):
    """Return dict of {law_key: new_value} for all non-excluded laws."""
    excluded = set(weights_data.get("excluded_law_categories", []))
    law_keys = schema.get("laws", [])
    props = schema.get("properties", {})

    primary_culture_id = str(nation.get("primary_culture", ""))
    culture = cultures_by_id.get(primary_culture_id, {})
    culture_traits = [
        culture.get("trait_one", "None"),
        culture.get("trait_two", "None"),
        culture.get("trait_three", "None"),
    ]

    new_laws = {}
    for law_key in law_keys:
        if law_key in excluded:
            continue
        options = props.get(law_key, {}).get("enum", [])
        if not options:
            continue
        new_laws[law_key] = _pick_law(options, law_key, culture_traits, weights_data)
    return new_laws


@admin_tool_routes.route("/randomize_ai_laws", methods=["GET"])
@admin_required
def randomize_ai_laws():
    schema, db = get_data_on_category("nations")
    player_ids = _get_player_nation_ids()
    ai_nations = list(db.find({"_id": {"$nin": list(player_ids)}}, {"name": 1, "primary_culture": 1}).sort("name", ASCENDING))
    weights_data = _load_law_weights()
    excluded = weights_data.get("excluded_law_categories", [])
    law_keys = [k for k in schema.get("laws", []) if k not in excluded]
    return render_template(
        "randomize_ai_laws.html",
        ai_nations=ai_nations,
        law_keys=law_keys,
        excluded=excluded,
    )


@admin_tool_routes.route("/randomize_ai_laws/preview", methods=["POST"])
@admin_required
def randomize_ai_laws_preview():
    schema, db = get_data_on_category("nations")
    player_ids = _get_player_nation_ids()
    ai_nations = list(db.find({"_id": {"$nin": list(player_ids)}}).sort("name", ASCENDING))
    weights_data = _load_law_weights()
    excluded = weights_data.get("excluded_law_categories", [])
    law_keys = [k for k in schema.get("laws", []) if k not in excluded]

    culture_ids = list({ObjectId(n["primary_culture"]) for n in ai_nations if n.get("primary_culture")})
    cultures_by_id = {
        str(c["_id"]): c
        for c in mongo.db.cultures.find({"_id": {"$in": culture_ids}}, {"trait_one": 1, "trait_two": 1, "trait_three": 1})
    }

    preview_rows = []
    for nation in ai_nations:
        new_laws = _randomize_nation_laws(nation, schema, weights_data, cultures_by_id)
        changes = {k: (nation.get(k, ""), v) for k, v in new_laws.items() if nation.get(k) != v}
        preview_rows.append({
            "name": nation.get("name", "?"),
            "culture": nation.get("primary_culture", ""),
            "changes": changes,
        })

    return render_template(
        "randomize_ai_laws.html",
        ai_nations=ai_nations,
        law_keys=law_keys,
        excluded=excluded,
        preview_rows=preview_rows,
    )


@admin_tool_routes.route("/randomize_ai_laws/apply", methods=["POST"])
@admin_required
def randomize_ai_laws_apply():
    from helpers.change_helpers import system_request_change, system_approve_change
    from copy import deepcopy

    schema, db = get_data_on_category("nations")
    player_ids = _get_player_nation_ids()
    ai_nations = list(db.find({"_id": {"$nin": list(player_ids)}}).sort("name", ASCENDING))
    weights_data = _load_law_weights()

    culture_ids = list({ObjectId(n["primary_culture"]) for n in ai_nations if n.get("primary_culture")})
    cultures_by_id = {
        str(c["_id"]): c
        for c in mongo.db.cultures.find({"_id": {"$in": culture_ids}}, {"trait_one": 1, "trait_two": 1, "trait_three": 1})
    }

    updated = 0
    for nation in ai_nations:
        new_laws = _randomize_nation_laws(nation, schema, weights_data, cultures_by_id)
        if not new_laws:
            continue
        old_data = deepcopy(nation)
        new_data = deepcopy(nation)
        new_data.update(new_laws)
        change_id = system_request_change(
            data_type="nations",
            item_id=nation["_id"],
            change_type="Update",
            before_data=old_data,
            after_data=new_data,
            reason="AI law randomization via admin tool",
        )
        system_approve_change(change_id)
        updated += 1

    flash(f"Randomized laws for {updated} AI nations.", "success")
    return redirect(url_for("admin_tool_routes.randomize_ai_laws"))


# ---------------------------------------------------------------------------
# AI Government Editor
# ---------------------------------------------------------------------------

_AI_GOV_FIELDS = ["government_type", "succession_type", "prosperity_role"]


@admin_tool_routes.route("/ai_government", methods=["GET"])
@admin_required
def ai_government():
    schema, db = get_data_on_category("nations")
    player_ids = _get_player_nation_ids()
    ai_nations = list(db.find(
        {"_id": {"$nin": list(player_ids)}},
        {"name": 1, "region": 1, "government_type": 1, "succession_type": 1, "prosperity_role": 1},
    ).sort("name", ASCENDING))

    region_ids = list({ObjectId(n["region"]) for n in ai_nations if n.get("region")})
    region_names = {
        str(r["_id"]): r.get("name", "Unknown")
        for r in mongo.db.regions.find({"_id": {"$in": region_ids}}, {"name": 1})
    }

    grouped = {}
    for nation in ai_nations:
        rname = region_names.get(str(nation.get("region", "")), "No Region")
        grouped.setdefault(rname, []).append(nation)

    props = schema.get("properties", {})
    enums = {f: props.get(f, {}).get("enum", []) for f in _AI_GOV_FIELDS}

    ai_nation_ids = [str(n["_id"]) for n in ai_nations]
    market_links = list(mongo.db.market_links.find(
        {"member": {"$in": ai_nation_ids}},
        {"member": 1, "market": 1, "market_safety_stance": 1},
    ))
    market_ids = list({ObjectId(ml["market"]) for ml in market_links if ml.get("market")})
    market_names = {
        str(m["_id"]): m.get("name", "?")
        for m in mongo.db.markets.find({"_id": {"$in": market_ids}}, {"name": 1})
    }
    nation_market_links = {}
    for ml in market_links:
        nation_market_links.setdefault(str(ml["member"]), []).append({
            "link_id": str(ml["_id"]),
            "market_name": market_names.get(str(ml.get("market", "")), "?"),
            "stance": ml.get("market_safety_stance", "Ignore"),
        })

    ml_schema = category_data.get("market_links", {}).get("schema", {})
    stance_enum = ml_schema.get("properties", {}).get("market_safety_stance", {}).get("enum", [])

    return render_template(
        "ai_government.html",
        grouped=grouped,
        enums=enums,
        fields=_AI_GOV_FIELDS,
        nation_market_links=nation_market_links,
        stance_enum=stance_enum,
    )


@admin_tool_routes.route("/ai_government/save", methods=["POST"])
@admin_required
def ai_government_save():
    from helpers.change_helpers import system_request_change, system_approve_change
    from copy import deepcopy

    schema, db = get_data_on_category("nations")
    player_ids = _get_player_nation_ids()
    ai_nations = list(db.find({"_id": {"$nin": list(player_ids)}}))
    nations_by_id = {str(n["_id"]): n for n in ai_nations}

    updated = 0
    for nation_id_str, nation in nations_by_id.items():
        changes = {}
        for field in _AI_GOV_FIELDS:
            form_key = f"{nation_id_str}__{field}"
            new_val = request.form.get(form_key)
            if new_val is not None and new_val != nation.get(field, ""):
                changes[field] = new_val
        if not changes:
            continue
        old_data = deepcopy(nation)
        new_data = deepcopy(nation)
        new_data.update(changes)
        change_id = system_request_change(
            data_type="nations",
            item_id=nation["_id"],
            change_type="Update",
            before_data=old_data,
            after_data=new_data,
            reason="AI government update via admin tool",
        )
        system_approve_change(change_id)
        updated += 1

    ml_updated = 0
    for key, new_stance in request.form.items():
        if not key.startswith("ml__"):
            continue
        link_id_str = key[4:]
        try:
            link_oid = ObjectId(link_id_str)
        except Exception:
            continue
        ml_doc = mongo.db.market_links.find_one({"_id": link_oid})
        if not ml_doc or ml_doc.get("market_safety_stance", "Ignore") == new_stance:
            continue
        old_ml = deepcopy(ml_doc)
        new_ml = deepcopy(ml_doc)
        new_ml["market_safety_stance"] = new_stance
        change_id = system_request_change(
            data_type="market_links",
            item_id=ml_doc["_id"],
            change_type="Update",
            before_data=old_ml,
            after_data=new_ml,
            reason="AI market stance update via admin tool",
        )
        system_approve_change(change_id)
        ml_updated += 1

    parts = []
    if updated:
        parts.append(f"{updated} nation(s)")
    if ml_updated:
        parts.append(f"{ml_updated} market stance(s)")
    flash(f"Updated {' and '.join(parts)}.", "success") if parts else flash("No changes.", "info")
    return redirect(url_for("admin_tool_routes.ai_government"))


# ---------------------------------------------------------------------------
# AI Goals Preview — read-only goal generation for debugging
# ---------------------------------------------------------------------------

@admin_tool_routes.route("/ai_goals_preview")
@admin_required
def ai_goals_preview():
    from helpers.ai_decision_helpers import (
        get_ai_personality, evaluate_nation_state,
        compute_need_weights, compute_upkeep_floor,
        select_strategic_goal, evaluate_goal_district,
        assign_goal_jobs, select_tech_target,
        generate_goal_trade_desires, get_stored_market_prices,
        score_buildable_districts, score_jobs, _weights_from_net,
        _future_resource_utility,
    )
    from calculations.field_calculations import calculate_all_fields
    from copy import deepcopy

    schema, db = get_data_on_category("nations")
    player_ids = _get_player_nation_ids()
    ai_names = [
        n["name"] for n in db.find(
            {"_id": {"$nin": list(player_ids)}}, {"name": 1, "_id": 0}
        ).sort("name", ASCENDING)
    ]
    if not ai_names:
        flash("No AI nations found.", "warning")
        return redirect(url_for("admin_tool_routes.admin_tools"))

    selected = request.args.get("nation", "")
    if selected not in ai_names:
        selected = random.choice(ai_names)

    cur_idx = ai_names.index(selected)
    prev_name = ai_names[cur_idx - 1]
    next_name = ai_names[(cur_idx + 1) % len(ai_names)]

    nation = db.find_one({"name": selected})
    region_name = ""
    if nation.get("region"):
        try:
            rdoc = mongo.db.regions.find_one({"_id": ObjectId(nation["region"])}, {"name": 1})
            region_name = rdoc.get("name", "") if rdoc else ""
        except Exception:
            pass

    error = None
    personality = {}
    state = {}
    need_weights = {}
    job_scores = {}
    upkeep_assignments = {}
    goal_assignments = {}
    upkeep_log = []
    goal_job_log = []
    upkeep_ratio = 0.0
    strategic_goal = {}
    secondary_goal = None
    future_utility = {}
    goal_candidates = []
    desires = []
    district_plan = None
    district_log = []
    district_scores = []
    resource_detail = {}
    tech_target = None
    primary_resource_keys = []
    luxury_keys = []
    mrp_attempts = []
    mrp_log = []

    try:
        calculated = calculate_all_fields(nation, schema, "nation")
        nation.update(calculated)

        personality = get_ai_personality(nation)
        state = evaluate_nation_state(nation)
        market_prices = get_stored_market_prices(nation)

        # Step 1: Upkeep floor (runs before need_weights so weights reflect post-upkeep reality)
        upkeep_assignments, remaining_pops, projected_net, upkeep_log, upkeep_ratio, _ = \
            compute_upkeep_floor(state, market_prices)

        # Compute need weights from post-upkeep production (no cap penalty here —
        # cap is enforced dynamically during job assignment loops)
        need_weights = _weights_from_net(
            projected_net, state["stockpiles"], market_prices, state["money_income"],
            active_resources=state.get("active_resources"), money_stock=state.get("money"),
        )
        job_scores = score_jobs(
            state, need_weights, market_prices,
            stability_headroom=state.get("stability_chance_headroom_remaining"),
        )

        # Step 2: Strategic goal
        strategic_goal, goal_candidates = select_strategic_goal(
            nation, state, personality, upkeep_ratio, market_prices
        )
        initial_goal = dict(strategic_goal)

        # Secondary goal (runner-up) + future construction utility — mirrors
        # ai_decision_tick so grow_economy weighting previews accurately.
        secondary_goal = next(
            (c for c in goal_candidates
             if c.get("type") != strategic_goal.get("type") and c.get("score", 0) > 0),
            None,
        )
        state["secondary_goal"] = secondary_goal
        future_utility = _future_resource_utility(state, secondary_goal, nation)
        state["future_utility"] = future_utility

        # Step 3: Goal-aware district (may build multiple, re-evaluates upkeep after each)
        dummy = deepcopy(nation)
        preview_log = []
        district_plan, district_scores, district_log, upkeep_assignments, strategic_goal = evaluate_goal_district(
            nation, dummy, state, strategic_goal, need_weights,
            market_prices, upkeep_assignments, preview_log, dry_run=True
        )

        # Re-compute upkeep floor with final state after district builds
        upkeep_assignments, remaining_pops, projected_net, _, upkeep_ratio, unresolved_deficits = \
            compute_upkeep_floor(state, market_prices)

        # Step 4: Goal-driven jobs
        goal_assignments, goal_job_log, final_projected_net = assign_goal_jobs(
            state, strategic_goal, remaining_pops, projected_net,
            district_plan, market_prices
        )

        # Step 4b: Tech target
        tech_dummy = deepcopy(nation)
        tech_target = select_tech_target(nation, tech_dummy, state, strategic_goal, personality)

        # Step 5: Trade desires (uses final projected_net including goal jobs)
        desires = generate_goal_trade_desires(
            state, strategic_goal, personality, district_plan,
            final_projected_net, market_prices, old_nation_ref=nation,
            upkeep_projected_net=projected_net, unresolved_deficits=unresolved_deficits
        )

        # Step 6: Mech RPs — dry_run=True so previewing never claims map tiles.
        # old_nation needs this preview's freshly-computed planned_district
        # (not the stale DB ai_state) for has_planned_district/target-resource
        # requirement checks, so build a shallow view carrying it.
        from helpers.mech_rp_helpers import select_mech_rps
        nation_for_mrp = dict(nation)
        nation_for_mrp["ai_state"] = {"planned_district": district_plan, "tech_target": tech_target}
        mrp_dummy = deepcopy(nation)
        mrp_attempts, mrp_log = select_mech_rps(
            nation_for_mrp, mrp_dummy, state, strategic_goal, secondary_goal,
            personality, schema, market_prices, dry_run=True,
        )

        from app_core import json_data as jd
        general_keys = [r["key"] for r in jd.get("general_resources", [])]
        unique_keys = [r["key"] for r in jd.get("unique_resources", [])]
        luxury_keys = [r["key"] for r in jd.get("luxury_resources", [])]
        primary_resource_keys = general_keys + unique_keys

        for r in state.get("net_production", {}):
            baseline_net = state["net_production"].get(r, 0)
            upkeep_net = projected_net.get(r, baseline_net)
            final_net = final_projected_net.get(r, upkeep_net)
            stock = state["stockpiles"].get(r, 0)
            sessions = state["sessions_until_empty"].get(r, float("inf"))
            resource_detail[r] = {
                "baseline_net": round(baseline_net, 2),
                "upkeep_net": round(upkeep_net, 2),
                "final_net": round(final_net, 2),
                "stock": round(stock, 1),
                "sessions_left": round(sessions, 1) if sessions != float("inf") else "inf",
                "weight": round(need_weights.get(r, 0), 2),
                "market_price": round(market_prices.get(r, 0), 1),
            }
        # Build diagnostic dump
        import json
        diag_data = {
            "_ai_code_version": "2026-06-23-v5",
            "nation": selected,
            "temperament": nation.get("temperament", "?"),
            "pops": state.get("total_pops", 0),
            "idle_pops": state.get("idle_pops", 0),
            "money": state.get("money", 0),
            "money_income": state.get("money_income", 0),
            "open_district_slots": state.get("open_district_slots", 0),
            "personality": {k: round(v, 2) for k, v in personality.items()},
            "initial_goal": initial_goal.get("type", "?") + f" ({initial_goal.get('score', 0)})",
            "strategic_goal": strategic_goal,
            "secondary_goal": secondary_goal,
            "future_utility": future_utility,
            "production_clamp_absorbed": state.get("production_clamp_absorbed", {}),
            "stability_chance_headroom": state.get("stability_chance_headroom", {}),
            "upkeep_ratio": round(upkeep_ratio, 2),
            "upkeep_assignments": upkeep_assignments,
            "goal_assignments": goal_assignments,
            "need_weights": {k: round(v, 2) for k, v in need_weights.items() if v != 1.3},
            "resource_state": {
                r: {"baseline": d["baseline_net"], "upkeep": d["upkeep_net"], "final": d["final_net"], "stock": d["stock"], "weight": d["weight"]}
                for r, d in resource_detail.items()
                if d["baseline_net"] != 0 or d["upkeep_net"] != 0 or d["final_net"] != 0 or d["stock"] > 0 or d["weight"] >= 3
            },
            "district_scores": [
                {"name": e[2], "score": round(e[0], 2), "rationale": e[4]}
                for e in (district_scores or [])[:12]
            ],
            "district_plan": {
                "name": district_plan.get("display_name", "?"),
                "cost": district_plan.get("cost", {}),
                "rationale": district_plan.get("rationale", ""),
                "source": district_plan.get("source", ""),
            } if district_plan else None,
            "district_log": district_log,
            "tech_target": tech_target,
            "desires": desires,
            "mech_rps": mrp_attempts,
        }
        diagnostic_json = json.dumps(diag_data, indent=2, default=str)

    except Exception as e:
        import traceback
        error = traceback.format_exc()
        diagnostic_json = ""

    return render_template(
        "ai_goals_preview.html",
        nation=nation,
        region_name=region_name,
        selected=selected,
        ai_names=ai_names,
        prev_name=prev_name,
        next_name=next_name,
        personality=personality,
        strategic_goal=strategic_goal,
        secondary_goal=secondary_goal,
        future_utility=future_utility,
        goal_candidates=goal_candidates,
        state=state,
        need_weights=need_weights,
        resource_detail=resource_detail,
        job_scores=job_scores,
        upkeep_assignments=upkeep_assignments,
        goal_assignments=goal_assignments,
        upkeep_log=upkeep_log,
        goal_job_log=goal_job_log,
        upkeep_ratio=upkeep_ratio,
        tech_target=tech_target,
        desires=desires,
        district_plan=district_plan,
        district_log=district_log,
        district_scores=district_scores,
        diagnostic_json=diagnostic_json,
        primary_resource_keys=primary_resource_keys,
        luxury_resource_keys=luxury_keys,
        mrp_attempts=mrp_attempts,
        mrp_log=mrp_log,
        error=error,
    )


@admin_tool_routes.route("/ai_goals_history")
@admin_required
def ai_goals_history():
    """Read-only viewer for the diagnostic snapshot saved by the most recent
    real AI decision tick (not a live re-run). Explains what the AI actually
    decided last session, since calculate_all_fields() may since have changed."""
    import json

    schema, db = get_data_on_category("nations")
    player_ids = _get_player_nation_ids()
    ai_names = [
        n["name"] for n in db.find(
            {"_id": {"$nin": list(player_ids)}}, {"name": 1, "_id": 0}
        ).sort("name", ASCENDING)
    ]
    if not ai_names:
        flash("No AI nations found.", "warning")
        return redirect(url_for("admin_tool_routes.admin_tools"))

    selected = request.args.get("nation", "")
    if selected not in ai_names:
        selected = random.choice(ai_names)

    cur_idx = ai_names.index(selected)
    prev_name = ai_names[cur_idx - 1]
    next_name = ai_names[(cur_idx + 1) % len(ai_names)]

    nation = db.find_one({"name": selected})
    diagnostic = (nation.get("ai_state") or {}).get("diagnostic")
    # Set directly by the separate "AI Mech RP Tick" (not nested in
    # diagnostic, which is only built by "AI Decision Tick").
    mrp_attempts = (nation.get("ai_state") or {}).get("mech_rps") or []

    primary_resource_keys = [r["key"] for r in json_data.get("general_resources", [])] + \
                             [r["key"] for r in json_data.get("unique_resources", [])]
    luxury_resource_keys = [r["key"] for r in json_data.get("luxury_resources", [])]

    diagnostic_json = json.dumps(diagnostic, indent=2, default=str) if diagnostic else ""

    return render_template(
        "ai_goals_history.html",
        nation=nation,
        selected=selected,
        ai_names=ai_names,
        prev_name=prev_name,
        next_name=next_name,
        diagnostic=diagnostic,
        mrp_attempts=mrp_attempts,
        diagnostic_json=diagnostic_json,
        primary_resource_keys=primary_resource_keys,
        luxury_resource_keys=luxury_resource_keys,
    )


# ---------------------------------------------------------------------------
# Bulk Add Modifiers
# ---------------------------------------------------------------------------

@admin_tool_routes.route("/admin/bulk_modifiers", methods=["GET"])
@admin_required
def bulk_modifiers():
    schema, db = get_data_on_category("nations")
    nations = list(db.find({}, {"name": 1, "region": 1, "_id": 1}).sort("name", ASCENDING))
    region_ids = list({ObjectId(n["region"]) for n in nations if n.get("region")})
    region_names = {
        str(r["_id"]): r.get("name", "Unknown")
        for r in mongo.db.regions.find({"_id": {"$in": region_ids}}, {"name": 1})
    }
    for n in nations:
        n["region_name"] = region_names.get(str(n.get("region", "")), "No Region")

    # modifier_types/scaling_types/scope_definitions/all_resources/all_jobs/
    # all_terrains etc. all come from the inject_modifier_data context
    # processor (routes/__init__.py) — the same source every other modifier
    # table (nation edit, district defs, etc.) uses, so this tool stays in
    # sync with them automatically.
    return render_template(
        "bulk_modifiers.html",
        nations=nations,
    )


@admin_tool_routes.route("/admin/bulk_modifiers/apply", methods=["POST"])
@admin_required
def bulk_modifiers_apply():
    from helpers.change_helpers import system_request_change, system_approve_change

    data = request.get_json() or {}
    modifiers = data.get("modifiers", [])
    nation_ids = data.get("nation_ids", [])

    if not modifiers or not nation_ids:
        return jsonify({"error": "No modifiers or nations selected"}), 400

    schema, db = get_data_on_category("nations")
    updated = 0
    for nid in nation_ids:
        try:
            oid = ObjectId(nid)
        except Exception:
            continue
        nation = db.find_one({"_id": oid})
        if not nation:
            continue
        old_data = deepcopy(nation)
        new_data = deepcopy(nation)
        existing = new_data.get("modifiers", [])
        if not isinstance(existing, list):
            existing = []
        for mod in modifiers:
            clean = {k: v for k, v in mod.items() if v is not None and v != ""}
            if "value" in clean:
                try:
                    clean["value"] = float(clean["value"])
                    if clean["value"] == int(clean["value"]):
                        clean["value"] = int(clean["value"])
                except (ValueError, TypeError):
                    pass
            if "duration" in clean:
                try:
                    clean["duration"] = int(clean["duration"])
                except (ValueError, TypeError):
                    clean["duration"] = -1
            if "scaling_x" in clean:
                try:
                    clean["scaling_x"] = float(clean["scaling_x"])
                except (ValueError, TypeError):
                    del clean["scaling_x"]
            if "max_value" in clean:
                try:
                    clean["max_value"] = float(clean["max_value"])
                except (ValueError, TypeError):
                    del clean["max_value"]
            existing.append(clean)
        new_data["modifiers"] = existing
        change_id = system_request_change(
            data_type="nations",
            item_id=nation["_id"],
            change_type="Update",
            before_data=old_data,
            after_data=new_data,
            reason="Bulk modifier addition via admin tool",
        )
        system_approve_change(change_id)
        updated += 1

    return jsonify({"ok": True, "updated": updated})


# ---------------------------------------------------------------------------
# Region Resource Adjustment
# ---------------------------------------------------------------------------

@admin_tool_routes.route("/admin/region_resource_adjustment", methods=["GET"])
@admin_required
def region_resource_adjustment():
    regions = list(mongo.db.regions.find({}, {"name": 1}).sort("name", ASCENDING))

    schema, db = get_data_on_category("nations")
    nation_region_counts = {}
    for nation in db.find({}, {"region": 1}):
        region_id = nation.get("region")
        if region_id:
            nation_region_counts[region_id] = nation_region_counts.get(region_id, 0) + 1
    for region in regions:
        region["nation_count"] = nation_region_counts.get(str(region["_id"]), 0)

    general_resources = json_data.get("general_resources", [])
    unique_resources = json_data.get("unique_resources", [])
    luxury_resources = json_data.get("luxury_resources", [])
    return render_template(
        "region_resource_adjustment.html",
        regions=regions,
        all_resources=general_resources + unique_resources + luxury_resources,
        general_keys={r["key"] for r in general_resources},
        unique_keys={r["key"] for r in unique_resources},
        luxury_keys={r["key"] for r in luxury_resources},
    )


@admin_tool_routes.route("/admin/region_resource_adjustment/apply", methods=["POST"])
@admin_required
def region_resource_adjustment_apply():
    from helpers.change_helpers import system_request_change, system_approve_change

    data = request.get_json() or {}
    region_id = (data.get("region_id") or "").strip()
    adjustments = data.get("adjustments") or {}
    reason = (data.get("reason") or "").strip()

    # Keep only non-zero, numeric adjustments — a resource left at 0 (or
    # blank) means "don't touch this resource" rather than "set it to 0".
    clean_adjustments = {}
    for key, val in adjustments.items():
        try:
            amt = float(val)
        except (ValueError, TypeError):
            continue
        if amt == int(amt):
            amt = int(amt)
        if amt != 0:
            clean_adjustments[key] = amt

    money_amt = 0
    try:
        money_amt = float(data.get("money") or 0)
    except (ValueError, TypeError):
        money_amt = 0
    if money_amt == int(money_amt):
        money_amt = int(money_amt)

    if not region_id:
        return jsonify({"error": "No region selected"}), 400
    if not clean_adjustments and not money_amt:
        return jsonify({"error": "No non-zero resource or money adjustments given"}), 400

    schema, db = get_data_on_category("nations")
    nations = list(db.find({"region": region_id}))
    if not nations:
        return jsonify({"error": "No nations found in that region"}), 400

    change_summary = dict(clean_adjustments)
    if money_amt:
        change_summary["money"] = money_amt
    default_reason = f"Bulk region resource adjustment via admin tool: {change_summary}"
    final_reason = f"{reason} ({default_reason})" if reason else default_reason

    updated = 0
    for nation in nations:
        old_data = deepcopy(nation)
        new_data = deepcopy(nation)
        # Flat, unclamped adjustment — deliberately allowed to go negative;
        # nothing here floors at 0 or caps at nation_resource_capacity.
        storage = dict(new_data.get("resource_storage", {}))
        for key, amt in clean_adjustments.items():
            storage[key] = storage.get(key, 0) + amt
        new_data["resource_storage"] = storage
        if money_amt:
            new_data["money"] = new_data.get("money", 0) + money_amt

        change_id = system_request_change(
            data_type="nations",
            item_id=nation["_id"],
            change_type="Update",
            before_data=old_data,
            after_data=new_data,
            reason=final_reason,
        )
        if change_id is not None:
            system_approve_change(change_id)
            updated += 1

    return jsonify({"ok": True, "updated": updated, "total": len(nations)})


# ---------------------------------------------------------------------------
# Clear Job Counts
# ---------------------------------------------------------------------------

@admin_tool_routes.route("/admin/clear_job_counts", methods=["GET"])
@admin_required
def clear_job_counts():
    jobs = sorted(
        ({"key": k, "name": v.get("display_name", k)} for k, v in json_data.get("jobs", {}).items()),
        key=lambda j: j["name"]
    )
    return render_template("admin/clear_job_counts.html", jobs=jobs)


@admin_tool_routes.route("/admin/clear_job_counts/apply", methods=["POST"])
@admin_required
def clear_job_counts_apply():
    """Reset the count of each selected job to 0 across every nation in the
    world, mirroring the job-count reset already done each tick by
    nation_job_cleanup_tick (keys are kept, just zeroed)."""
    selected_jobs = request.form.getlist("jobs")
    if not selected_jobs:
        flash("No jobs selected.", "error")
        return redirect(url_for("admin_tool_routes.clear_job_counts"))

    set_fields = {f"jobs.{job_key}": 0 for job_key in selected_jobs}
    query = {"$or": [{f"jobs.{job_key}": {"$exists": True}} for job_key in selected_jobs]}
    result = mongo.db.nations.update_many(query, {"$set": set_fields})

    flash(f"Cleared {len(selected_jobs)} job type(s) across {result.modified_count} nation(s).", "success")
    return redirect(url_for("admin_tool_routes.clear_job_counts"))


# ---------------------------------------------------------------------------
# Archived Change Search
#
# Changes older than ~20 sessions are exported to S3 as JSON files and
# deleted from MongoDB (see helpers/archive_helpers.py). This tool scans
# those exported files directly since they're no longer queryable in the DB.
# ---------------------------------------------------------------------------

def _parse_change_dt(value):
    """Return a timezone-aware datetime from either a datetime object or an
    ISO-format string (changes store their timestamp fields as strings)."""
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)
    if isinstance(value, str):
        normalized = value.rstrip("Z")
        try:
            dt = datetime.datetime.fromisoformat(normalized)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
    return None


def _change_relevant_date(doc):
    """Pick the most meaningful date field for a change based on its status
    (mirrors the same status->field preference used when originally deciding
    what's old enough to archive)."""
    status = doc.get("status")
    field = {
        "Approved": "time_implemented",
        "Denied": "time_denied",
        "Reverted": "time_reverted",
    }.get(status, "last_modified_time")
    return _parse_change_dt(doc.get(field)) or _parse_change_dt(doc.get("last_modified_time"))


def _resolve_target_ids_by_name(target_collection, target_name):
    """Return a set of string _ids in target_collection whose name matches
    target_name (case-insensitive substring), or None if target_name/collection
    is missing (meaning: don't restrict by target object)."""
    if not target_collection or not target_name or target_collection not in category_data:
        return None
    import re as _re
    try:
        db_ = category_data[target_collection]["database"]
        docs = db_.find(
            {"name": {"$regex": _re.escape(target_name), "$options": "i"}}, {"_id": 1}
        )
        return {str(d["_id"]) for d in docs}
    except Exception:
        return set()


def _search_archived_changes(start_date, end_date, requester_id, approver_id,
                              target_collection="", target_name=""):
    """Scan every changes_archive_*.json file in the S3 backups/ folder and
    return (matches, files_scanned). Filters are all optional/AND'd together."""
    import boto3
    from bson import json_util

    s3_bucket = os.getenv("S3_BUCKET_NAME")
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    if not (s3_bucket and aws_access_key and aws_secret_key):
        raise RuntimeError("S3 is not configured (missing bucket/credentials).")

    s3_client = boto3.client(
        "s3",
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
    )

    start_dt = None
    end_dt = None
    if start_date:
        start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
    if end_date:
        end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=datetime.timezone.utc
        )

    # Compare IDs as plain hex strings rather than ObjectId instances: archived
    # documents inconsistently store requester/approver as either a raw ObjectId
    # or a plain string depending on when the change was originally created, and
    # str(ObjectId(...)) always matches the hex string form either way.
    requester_id = requester_id or None
    approver_id = approver_id or None
    target_collection = target_collection or None
    target_ids = _resolve_target_ids_by_name(target_collection, target_name)

    keys = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=s3_bucket, Prefix="backups/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if "changes_archive" in key and key.endswith(".json"):
                keys.append(key)

    matches = []
    for key in keys:
        try:
            obj = s3_client.get_object(Bucket=s3_bucket, Key=key)
            docs = json_util.loads(obj["Body"].read().decode("utf-8"))
        except Exception:
            continue
        for doc in docs:
            if requester_id and str(doc.get("requester", "")) != requester_id:
                continue
            if approver_id and str(doc.get("approver", "")) != approver_id:
                continue
            if target_collection and doc.get("target_collection") != target_collection:
                continue
            if target_ids is not None and str(doc.get("target", "")) not in target_ids:
                continue
            change_dt = _change_relevant_date(doc)
            if start_dt and (change_dt is None or change_dt < start_dt):
                continue
            if end_dt and (change_dt is None or change_dt > end_dt):
                continue
            doc["_archive_file"] = key
            doc["_display_date"] = change_dt
            matches.append(doc)

    matches.sort(key=lambda d: d.get("_display_date") or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), reverse=True)
    return matches, len(keys)


def _search_db_archived_changes(start_date, end_date, requester_id, approver_id,
                                 target_collection="", target_name=""):
    """Search non-pending changes still resident in MongoDB — i.e. changes
    recent enough (within ~20 sessions) that archive_helpers.py hasn't yet
    exported them to S3 and deleted them from the DB. A live query, so this
    is fast enough to run synchronously (no background job needed)."""
    query = {"status": {"$ne": "Pending"}}
    # Defensive $in on both ObjectId and raw string form: some legacy changes
    # store requester/approver as a plain string rather than an ObjectId.
    if requester_id:
        try:
            query["requester"] = {"$in": [ObjectId(requester_id), requester_id]}
        except Exception:
            query["requester"] = requester_id
    if approver_id:
        try:
            query["approver"] = {"$in": [ObjectId(approver_id), approver_id]}
        except Exception:
            query["approver"] = approver_id
    if target_collection:
        query["target_collection"] = target_collection

    target_ids = _resolve_target_ids_by_name(target_collection, target_name)

    start_dt = None
    end_dt = None
    if start_date:
        start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
    if end_date:
        end_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=datetime.timezone.utc
        )

    matches = []
    for doc in mongo.db.changes.find(query):
        if target_ids is not None and str(doc.get("target", "")) not in target_ids:
            continue
        change_dt = _change_relevant_date(doc)
        if start_dt and (change_dt is None or change_dt < start_dt):
            continue
        if end_dt and (change_dt is None or change_dt > end_dt):
            continue
        doc["_display_date"] = change_dt
        matches.append(doc)

    matches.sort(key=lambda d: d.get("_display_date") or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), reverse=True)
    return matches


# Archive files can be very large (one existing export is 300+ MB, taking 40-50s
# just to download), which exceeds typical platform request timeouts (e.g. Heroku's
# hard 30s router limit). The search runs in a background thread; the page polls
# a small in-memory job store until it's done. Single-process Flask app (app.run),
# so a plain module-level dict is safely shared across all requests.
_ARCHIVE_SEARCH_JOBS = {}
_ARCHIVE_SEARCH_JOBS_MAX = 20
_ARCHIVE_SEARCH_RESULTS_CAP = 500


def _build_change_display_context(results):
    """Build target_schemas + preview_references so results can be rendered
    with the exact same render_changes_table macro used by the live
    Pending/Archived Changes list pages (resolves target/requester/etc. links
    and drives the before/after diff view)."""
    from routes.change_routes import get_preview_references

    changes_schema = category_data["changes"]["schema"]
    target_schemas = {name: data["schema"] for name, data in category_data.items()}

    collections_to_preview = {}
    for preview_item in changes_schema.get("preview", []):
        collection_names = changes_schema.get("properties", {}).get(preview_item, {}).get("collections")
        if collection_names:
            for collection_name in collection_names:
                collections_to_preview[preview_item] = collection_name

    for doc in results:
        tc = doc.get("target_collection")
        if tc in category_data:
            collections_to_preview[tc] = tc
            for field_schema in target_schemas.get(tc, {}).get("properties", {}).values():
                if field_schema.get("bsonType") == "linked_object" and field_schema.get("collections"):
                    for linked_collection in field_schema["collections"]:
                        collections_to_preview[linked_collection] = linked_collection

    preview_references = get_preview_references(changes_schema, collections_to_preview)
    return target_schemas, preview_references


def _run_archive_search_job(job_id, start_date, end_date, requester_id, approver_id,
                             target_collection, target_name):
    try:
        results, files_scanned = _search_archived_changes(
            start_date, end_date, requester_id, approver_id, target_collection, target_name
        )
        capped = results[:_ARCHIVE_SEARCH_RESULTS_CAP]
        target_schemas, preview_references = _build_change_display_context(capped)
        _ARCHIVE_SEARCH_JOBS[job_id] = {
            "status": "done",
            "source": "s3",
            "results": capped,
            "target_schemas": target_schemas,
            "preview_references": preview_references,
            "total_count": len(results),
            "files_scanned": files_scanned,
        }
    except Exception as e:
        _ARCHIVE_SEARCH_JOBS[job_id] = {"status": "error", "error": str(e)}


@admin_tool_routes.route("/archived_changes_search")
@admin_required
def archived_changes_search():
    players = list(mongo.db.players.find({}, {"name": 1}).sort("name", ASCENDING))
    target_collections = sorted(mongo.db.changes.distinct("target_collection"))

    job_id = request.args.get("job", "").strip()
    job = _ARCHIVE_SEARCH_JOBS.get(job_id) if job_id else None

    source = (job.get("source") if job else None) or request.args.get("source", "database")

    return render_template(
        "archived_changes_search.html",
        players=players,
        target_collections=target_collections,
        start_date=request.args.get("start_date", ""),
        end_date=request.args.get("end_date", ""),
        requester_id=request.args.get("requester", ""),
        approver_id=request.args.get("approver", ""),
        target_collection=request.args.get("target_collection", ""),
        target_name=request.args.get("target_name", ""),
        source=source,
        job_id=job_id,
        job=job,
        results_cap=_ARCHIVE_SEARCH_RESULTS_CAP,
    )


@admin_tool_routes.route("/archived_changes_search/start", methods=["POST"])
@admin_required
def archived_changes_search_start():
    import uuid
    from threading import Thread

    source = request.form.get("source", "database").strip()
    start_date = request.form.get("start_date", "").strip()
    end_date = request.form.get("end_date", "").strip()
    requester_id = request.form.get("requester", "").strip()
    approver_id = request.form.get("approver", "").strip()
    target_collection = request.form.get("target_collection", "").strip()
    target_name = request.form.get("target_name", "").strip()

    job_id = uuid.uuid4().hex

    # Bound memory: drop the oldest job(s) once we exceed the cap.
    if len(_ARCHIVE_SEARCH_JOBS) > _ARCHIVE_SEARCH_JOBS_MAX:
        for old_id in list(_ARCHIVE_SEARCH_JOBS.keys())[: len(_ARCHIVE_SEARCH_JOBS) - _ARCHIVE_SEARCH_JOBS_MAX]:
            _ARCHIVE_SEARCH_JOBS.pop(old_id, None)

    if source == "s3":
        _ARCHIVE_SEARCH_JOBS[job_id] = {"status": "running", "source": "s3"}
        thread = Thread(
            target=_run_archive_search_job,
            args=(job_id, start_date, end_date, requester_id, approver_id,
                  target_collection, target_name),
        )
        thread.daemon = True
        thread.start()
    else:
        # Database search is a live query — fast enough to run synchronously
        # and store as an already-"done" job, reusing the same results view.
        try:
            results = _search_db_archived_changes(
                start_date, end_date, requester_id, approver_id, target_collection, target_name
            )
            capped = results[:_ARCHIVE_SEARCH_RESULTS_CAP]
            target_schemas, preview_references = _build_change_display_context(capped)
            _ARCHIVE_SEARCH_JOBS[job_id] = {
                "status": "done",
                "source": "database",
                "results": capped,
                "target_schemas": target_schemas,
                "preview_references": preview_references,
                "total_count": len(results),
            }
        except Exception as e:
            _ARCHIVE_SEARCH_JOBS[job_id] = {"status": "error", "error": str(e)}

    return redirect(url_for(
        "admin_tool_routes.archived_changes_search",
        job=job_id, start_date=start_date, end_date=end_date,
        requester=requester_id, approver=approver_id, source=source,
        target_collection=target_collection, target_name=target_name,
    ))
