# app_core.py
from flask import Flask
from flask_pymongo import PyMongo
from flask_discord import DiscordOAuth2Session
import os
import json
import re
from dotenv import load_dotenv
import subprocess
import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.utils import formatdate
from email import encoders
import boto3
from threading import Thread
from pymongo import ASCENDING, DESCENDING

load_dotenv(override=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecretkey")

from datetime import timedelta
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

# Allow negative integers in URL routes (needed for flat-top hex axial coordinates)
from werkzeug.routing import IntegerConverter
class SignedIntConverter(IntegerConverter):
    regex = r'-?\d+'
app.url_map.converters['signed_int'] = SignedIntConverter

# Discord config
app.config["DISCORD_CLIENT_ID"] = os.getenv("DISCORD_CLIENT_ID")
app.config["DISCORD_CLIENT_SECRET"] = os.getenv("DISCORD_CLIENT_SECRET")
app.config["DISCORD_REDIRECT_URI"] = os.getenv("DISCORD_REDIRECT_URI")

# Mongo config
app.config["MONGO_URI"] = os.getenv("MONGO_URI")
mongo = PyMongo(app)
discord = DiscordOAuth2Session(app)

@app.template_filter('from_json')
def from_json_filter(value):
    try:
        return json.loads(value) if isinstance(value, str) else (value or [])
    except (json.JSONDecodeError, TypeError):
        return []

@app.template_filter('format_discord_link')
def format_discord_link(text):
    # Pattern for Discord message links
    discord_pattern = r'(https://(?:ptb\.|canary\.)?discord\.com/channels/\d+/\d+/\d+)'
    
    def replace_link(match):
        full_url = match.group(1)
        # Extract the last part of the URL (message ID) for display
        message_id = full_url.split('/')[-1]
        return f'<a href="{full_url}" target="_blank">Discord Message ({message_id})</a>'
    
    # Replace Discord links with formatted HTML
    formatted_text = re.sub(discord_pattern, replace_link, str(text))
    return formatted_text

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
    "market_links": {"pluralName": "Market Links", "singularName": "Market Link", "database": mongo.db.market_links},
    "wars": {"pluralName": "Wars", "singularName": "War", "database": mongo.db.wars},
    "diplo_relations": {"pluralName": "Diplomatic Relations", "singularName": "Diplomatic Relation", "database": mongo.db.diplo_relations},
    "pops": {"pluralName": "Pops", "singularName": "Pop", "database": mongo.db.pops},
    "events": {"pluralName": "Events", "singularName": "Event", "database": mongo.db.events},
    "changes": {"pluralName": "Changes", "singularName": "Change", "database": mongo.db.changes},
    "global_modifiers": {"pluralName": "Global Modifiers", "singularName": "Global Modifiers", "database": mongo.db.global_modifiers},
    "units": {"pluralName": "Units", "singularName": "Unit", "database": mongo.db.units},
    "traits": {"pluralName": "Traits", "singularName": "Trait", "database": mongo.db.traits},
    "trade_routes": {"pluralName": "Trade Routes", "singularName": "Trade Route", "database": mongo.db.trade_routes},
    "titles": {"pluralName": "Titles", "singularName": "Title", "database": mongo.db.titles},
    "hex_map": {"pluralName": "Map Changes", "singularName": "Map Change", "database": mongo.db.hex_map_tiles},
}

def ensure_mongo_indexes():
    index_specs = {
        "nations": [[("name", ASCENDING)]],
        "pops": [[("nation", ASCENDING)]],
        "characters": [[("ruling_nation_org", ASCENDING)], [("player", ASCENDING)]],
        "diplo_relations": [[("nation_1", ASCENDING)], [("nation_2", ASCENDING)]],
        "wonders":       [[("owner_nation", ASCENDING)]],
        "hex_map_tiles":  [[("q", ASCENDING), ("r", ASCENDING)], [("owner", ASCENDING)]],
        "hex_map_history": [[("session", ASCENDING)]],
        "district_defs":       [[("key", ASCENDING)], [("category", ASCENDING), ("tier", ASCENDING)]],
        "district_categories": [[("key", ASCENDING)]],
        "changes": [[("last_modified_time", DESCENDING), ("time_requested", DESCENDING)]],
    }

    for collection_name, specs in index_specs.items():
        collection = mongo.db[collection_name]
        for spec in specs:
            try:
                collection.create_index(spec)
            except Exception as e:
                print(f"Index creation failed for {collection_name} {spec}: {e}")

rarity_rankings = {"Mythical": 0, "Legendary": 1, "Great": 2, "Good": 3, "Mundane": 4}

json_files = ["jobs", "tech", "nation_imperial_districts", "mercenary_districts",
                "merchant_production_districts", "merchant_specialty_districts", "merchant_luxury_districts",
                "cities", "terrains", "walls", "positive_titles", "negative_titles", "meta_mods", "modifier_types",
                "scaling_types", "scope_definitions", "war_types", "war_goals"]
json_data = {"general_resources": [
            # color: optional hex string for map node rendering; omit to auto-generate
            {"key": "food",     "name": "Food",     "base_storage": 25, "base_price": 50,  "color": "#00ff00"},
            {"key": "wood",     "name": "Wood",     "base_storage": 15, "base_price": 100,  "color": "#b47e5a"},
            {"key": "stone",    "name": "Stone",    "base_storage": 15, "base_price": 100,  "color": "#96e3ef"},
            {"key": "mounts",   "name": "Mounts",   "base_storage": 15, "base_price": 100,  "color": "#ffeb00"},
            {"key": "research", "name": "Research", "base_storage": 0,                     "color": "#ff00b8"},
            {"key": "magic",    "name": "Magic",    "base_storage": 15, "base_price": 125, "color": "#ba00ff"},
        ],
        "unique_resources": [
            {"key": "iron",   "name": "Iron",   "base_storage": 10,  "base_price": 200, "color": "#828282"},
            {"key": "gunpowder", "name": "Gunpowder", "base_storage": 0,  "base_price": 400, "color": "#d9ba2a"},
        ],
        "luxury_resources": [
            # Luxury resources render as squares on the map; nodes produce 1/session if they are worked by a building
            {"key": "narcotics",       "name": "Narcotics",       "base_storage": 10, "base_price": 400, "color": "#f6092f"},
            {"key": "spices",          "name": "Spices",          "base_storage": 10, "base_price": 400, "color": "#e2620c"},
            {"key": "medicinal_herbs", "name": "Medicinal Herbs", "base_storage": 10, "base_price": 400, "color": "#0b9d11"},
            {"key": "dyes",            "name": "Dyes",            "base_storage": 10, "base_price": 400, "color": "#ff009c"},
            {"key": "magical_crystals","name": "Magical Crystals","base_storage": 10, "base_price": 400, "color": "#00ffcc"},
            {"key": "gold",            "name": "Gold",            "base_storage": 10, "base_price": 600, "color": "#f1c232"},
            {"key": "moonstone",       "name": "Moonstone",       "base_storage": 10, "base_price": 400, "color": "#ead1dc"},
            {"key": "furs",            "name": "Furs",            "base_storage": 10, "base_price": 400, "color": "#fcb893"},
            {"key": "quintessence",    "name": "Quintessence",    "base_storage": 10, "base_price": 400, "color": "#d379ff"},
            {"key": "cocoa",           "name": "Cocoa",           "base_storage": 10, "base_price": 400, "color": "#783200"},
            {"key": "star_iron",       "name": "Star Iron",       "base_storage": 10, "base_price": 400, "color": "#9fc5e8"},
            {"key": "tea",             "name": "Tea",             "base_storage": 10, "base_price": 400, "color": "#b6d7a8"},
        ],
        "slot_types": {
            "no_slot":            {"progress_per_tick": 0, "name": "No Slot"},
            "0_progress_slot":    {"progress_per_tick": 0, "name": "0 Progress Slot"},
            "1_progress_slot":    {"progress_per_tick": 1, "name": "1 Progress Slot"},
            "2_progress_slot":    {"progress_per_tick": 2, "name": "2 Progress Slot"},
            "3_progress_slot":    {"progress_per_tick": 3, "name": "3 Progress Slot"},
            "4_progress_slot":    {"progress_per_tick": 4, "name": "4 Progress Slot"},
            "tier_1_spell_slot":  {"progress_per_tick": 0, "name": "Tier 1 Spell Slot"},
            "tier_2_spell_slot":  {"progress_per_tick": 0, "name": "Tier 2 Spell Slot"},
            "tier_3_spell_slot":  {"progress_per_tick": 0, "name": "Tier 3 Spell Slot"},
        },
        "pops_per_district_slot": 10
}

temperament_enum = ["Player", "Neutral", "Welcoming", "Aggressive", "Withdrawn", "Curious", "Supremacist", "Zealous"]

base_temperament_odds = {
    "Player": 0,
    "Neutral": 0.15,
    "Welcoming": 0.2,
    "Aggressive": 0.2,
    "Withdrawn": 0.1,
    "Curious": 0.15,
    "Supremacist": 0.1,
    "Zealous": 0.1
}

cultural_trait_temperament_modifiers = {
    "Absolutist": {"Player": 0, "Neutral": 0, "Welcoming": -0.05, "Aggressive": 0, "Withdrawn": 0, "Curious": -0.05, "Supremacist": 0.1, "Zealous": 0},
    "Communalist": {"Player": 0, "Neutral": -0.05, "Welcoming": 0.05, "Aggressive": 0, "Withdrawn": 0.05, "Curious": -0.05, "Supremacist": 0, "Zealous": 0},
    "Egalitarian": {"Player": 0, "Neutral": 0, "Welcoming": 0.05, "Aggressive": 0.05, "Withdrawn": -0.05, "Curious": 0, "Supremacist": -0.05, "Zealous": 0},
    "Individualist": {"Player": 0, "Neutral": 0, "Welcoming": 0, "Aggressive": 0, "Withdrawn": 0.05, "Curious": 0.05, "Supremacist": -0.05, "Zealous": -0.05},
    "Isolationist": {"Player": 0, "Neutral": -0.05, "Welcoming": 0, "Aggressive": 0, "Withdrawn": 0.2, "Curious": -0.05, "Supremacist": -0.05, "Zealous": -0.05},
    "Mercantilist": {"Player": 0, "Neutral": 0, "Welcoming": 0.09, "Aggressive": -0.09, "Withdrawn": -0.09, "Curious": 0.09, "Supremacist": 0, "Zealous": 0},
    "Militarist": {"Player": 0, "Neutral": -0.06, "Welcoming": -0.09, "Aggressive": 0.11, "Withdrawn": -0.06, "Curious": 0, "Supremacist": 0.05, "Zealous": 0.05},
    "Pacifist": {"Player": 0, "Neutral": -0.05, "Welcoming": 0.09, "Aggressive": -0.09, "Withdrawn": 0, "Curious": 0, "Supremacist": 0, "Zealous": 0},
    "Progressive": {"Player": 0, "Neutral": -0.05, "Welcoming": 0, "Aggressive": 0, "Withdrawn": -0.05, "Curious": 0.1, "Supremacist": 0, "Zealous": 0},
    "Secular": {"Player": 0, "Neutral": -0.05, "Welcoming": 0.04, "Aggressive": 0, "Withdrawn": 0, "Curious": 0.06, "Supremacist": 0.04, "Zealous": -0.09},
    "Spiritualist": {"Player": 0, "Neutral": -0.05, "Welcoming": 0, "Aggressive": 0, "Withdrawn": 0, "Curious": -0.05, "Supremacist": 0, "Zealous": 0.1},
    "Traditionalist": {"Player": 0, "Neutral": 0, "Welcoming": 0, "Aggressive": 0, "Withdrawn": 0, "Curious": -0.09, "Supremacist": 0.05, "Zealous": 0.04}
}

character_stats = ["rulership", "cunning", "charisma", "prowess", "magic", "strategy"]

def load_json(file_path):
    with open(file_path, "r") as file:
        json_data = json.load(file)
    return json_data

def find_dict_in_list(dict_list, key_field, key_value):
    """
    Finds a dictionary in a list by matching a key field.
    
    Args:
        dict_list: List of dictionaries to search through
        key_field: The field name to match on (e.g., 'key', 'name')
        key_value: The value to match against
        
    Returns:
        The matching dictionary or None if not found
    """
    if not dict_list or not isinstance(dict_list, list):
        return None
    
    for item in dict_list:
        if isinstance(item, dict) and key_field in item and item[key_field] == key_value:
            return item
    
    return None

for data_type in category_data:
    category_data[data_type]["schema"] = load_json("json-data/schemas/" + data_type + ".json")["$jsonSchema"]

for file in json_files:
    json_data[file] = load_json("json-data/" + file + ".json")

ensure_mongo_indexes()

def upload_bytes_to_s3(file_bytes, s3_key, content_type="image/jpeg"):
    """Upload file bytes directly to S3 (e.g. from a Flask file upload).
    Returns (success, public_url_or_error_message).
    """
    try:
        from io import BytesIO
        s3_bucket = os.getenv("S3_BUCKET_NAME")
        aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

        if not s3_bucket or not aws_access_key or not aws_secret_key:
            return False, "S3 configuration missing"

        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key
        )
        s3_client.upload_fileobj(
            BytesIO(file_bytes),
            s3_bucket,
            s3_key,
            ExtraArgs={"ContentType": content_type}
        )
        return True, f"/api/s3-image/{s3_key}"
    except Exception as e:
        return False, f"S3 upload failed: {str(e)}"


def upload_to_s3(file_path, s3_key):
    """Upload a file to S3 bucket"""
    try:
        
        # S3 configuration
        s3_bucket = os.getenv("S3_BUCKET_NAME")
        aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        
        if not s3_bucket or not aws_access_key or not aws_secret_key:
            print("S3 upload skipped: missing S3 configuration.")
            return False, "S3 configuration missing"
        
        # Create S3 client
        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key
        )
        
        # Upload file
        s3_client.upload_file(file_path, s3_bucket, s3_key)
        
        print(f"S3 upload success: {s3_bucket}/{s3_key}")
        return True, f"File uploaded to S3: {s3_bucket}/{s3_key}"
    except Exception as e:
        print(f"S3 upload failed: {str(e)}")
        return False, f"S3 upload failed: {str(e)}"

def backup_mongodb():
    """
    Creates a backup of the MongoDB database and sends it via email.
    Uses PyMongo directly instead of mongodump for Heroku compatibility.
    Returns a tuple of (success, message)
    """
    try:
        # Create backup directory if it doesn't exist
        backup_dir = os.path.join(os.getcwd(), 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        
        # Generate timestamp for the backup filename
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f"mongodb_backup_{timestamp}"
        backup_path = os.path.join(backup_dir, backup_filename)
        os.makedirs(backup_path, exist_ok=True)
        
        # Get MongoDB connection details
        mongo_uri = os.getenv("MONGO_URI", "")
        
        # Properly extract database name from URI without query parameters
        from urllib.parse import urlparse
        
        # Parse the MongoDB URI
        parsed_uri = urlparse(mongo_uri)
        
        # Extract just the database name (path without leading slash)
        db_name = parsed_uri.path.lstrip('/')
        
        # If there's a question mark in the db_name, only take what's before it
        if '?' in db_name:
            db_name = db_name.split('?')[0]
        
        print(f"Backing up {db_name} to {backup_path}")

        # Use PyMongo to create backup
        from pymongo import MongoClient
        import json
        
        client = MongoClient(
            mongo_uri,
            serverSelectionTimeoutMS=60000,
            connectTimeoutMS=60000,
            socketTimeoutMS=600000,
            wTimeoutMS=600000
        )
        db = client[db_name]
        
        # Get all collections
        collections = db.list_collection_names()
        
        # Create a directory for each collection
        for collection_name in collections:
            collection_dir = os.path.join(backup_path, db_name)
            os.makedirs(collection_dir, exist_ok=True)
            
            # Get collection reference
            collection = db[collection_name]
            
            # Stream documents in batches to reduce memory usage
            batch_size = 1000
            file_path = os.path.join(collection_dir, f"{collection_name}.json")
            
            with open(file_path, 'w') as f:
                f.write('[\n')
                first_doc = True
                
                # Use cursor with batch processing
                cursor = collection.find({}).batch_size(batch_size)
                
                for doc in cursor:
                    # Convert ObjectId to string for JSON serialization
                    if '_id' in doc and hasattr(doc['_id'], '__str__'):
                        doc['_id'] = str(doc['_id'])
                    
                    # Convert any other ObjectId fields
                    for key, value in doc.items():
                        if hasattr(value, '__str__') and str(type(value)) == "<class 'bson.objectid.ObjectId'>":
                            doc[key] = str(value)
                    
                    # Write document to file immediately
                    if not first_doc:
                        f.write(',\n')
                    json.dump(doc, f, default=str, indent=2)
                    first_doc = False
                
                f.write('\n]')
        
        # Create a zip file of the backup with compression
        import zipfile
        
        zip_path = f"{backup_path}.zip"
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
            for root, dirs, files in os.walk(backup_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, os.path.dirname(backup_path))
                    zipf.write(file_path, arcname)

        # Send email with the backup attached
        email_success, email_message = send_backup_email(zip_path, db_name, timestamp)
        
        # Clean up the unzipped directory to save space on Heroku
        import shutil
        shutil.rmtree(backup_path)
        
        # Upload to S3 if configured
        s3_success, s3_message = upload_to_s3(zip_path, f"backups/{os.path.basename(zip_path)}")
        
        # Clean up local zip file after S3 upload to save space
        if s3_success and os.path.exists(zip_path):
            os.remove(zip_path)
        
        return True, f"Backup created successfully. Email: {email_message}. S3: {s3_message}"

    except Exception as e:
        return False, f"Backup failed: {str(e)}"

def backup_mongodb_async():
    """
    Starts a MongoDB backup in a background thread.
    Returns a tuple of (success, message).
    """
    try:
        def _run_backup_and_log():
            success, message = backup_mongodb()
            status = "success" if success else "failure"
            print(f"Backup completed with {status}: {message}")

        thread = Thread(target=_run_backup_and_log)
        thread.daemon = True
        thread.start()
        return True, "Backup process started in background. Check logs for results."
    except Exception as e:
        return False, f"Failed to start backup thread: {str(e)}"

def send_backup_email(attachment_path, db_name, timestamp):
    """
    Sends an email with the backup file attached.
    """
    try:
        # Email configuration
        email_from = os.getenv("EMAIL_FROM", "")
        email_to = os.getenv("EMAIL_TO", "")
        email_password = os.getenv("EMAIL_PASSWORD", "")
        smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        
        if not email_from or not email_to or not email_password:
            return False, "Email configuration missing"
        
        # Create message
        msg = MIMEMultipart()
        msg['From'] = email_from
        msg['To'] = email_to
        msg['Date'] = formatdate(localtime=True)
        msg['Subject'] = f"MongoDB Backup - {db_name} - {timestamp}"
        
        # Email body
        body = f"Attached is the MongoDB backup for {db_name} created on {timestamp}."
        msg.attach(MIMEText(body))
        
        # Attach the backup file
        with open(attachment_path, 'rb') as file:
            part = MIMEBase('application', 'zip')
            part.set_payload(file.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(attachment_path)}"')
            msg.attach(part)
        
        # Send the email
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(email_from, email_password)
            server.send_message(msg)
        
        return True, "Email sent successfully"
    
    except Exception as e:
        return False, f"Email failed: {str(e)}"

def restore_mongodb(backup_path=None, backup_date=None, s3_key=None, s3_bucket=None):
    """
    Restores MongoDB database from a backup.
    Uses PyMongo directly instead of mongorestore for Heroku compatibility.
    
    Args:
        backup_path: Direct path to the backup zip file
        backup_date: Date string in format 'YYYYMMDD_HHMMSS' to find a specific backup
        s3_key: S3 object key for the backup file
        s3_bucket: S3 bucket name containing the backup
        
    Returns:
        tuple: (success, message)
    """
    try:
        backup_dir = os.path.join(os.getcwd(), 'backups')
        
        # If S3 backup is specified, download it first
        if s3_key and s3_bucket:
            try:
                # Create S3 client with retries and generous timeouts
                from botocore.config import Config as BotoConfig
                from boto3.s3.transfer import TransferConfig as S3TransferConfig

                s3_client = boto3.client(
                    's3',
                    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                    config=BotoConfig(
                        retries={'max_attempts': 8, 'mode': 'standard'},
                        connect_timeout=30,
                        read_timeout=300
                    )
                )
                
                # Ensure backup directory exists
                os.makedirs(backup_dir, exist_ok=True)
                
                # Set local path for downloaded file
                local_filename = os.path.basename(s3_key)
                local_path = os.path.join(backup_dir, local_filename)
                
                # Download the file with multipart transfer and limited concurrency (reduce memory)
                print(f"Downloading {s3_key} from S3 bucket {s3_bucket} to {local_path}")
                transfer_cfg = S3TransferConfig(
                    multipart_threshold=8 * 1024 * 1024,  # 8MB
                    multipart_chunksize=8 * 1024 * 1024,   # 8MB chunks
                    max_concurrency=4,                    # fewer threads to reduce memory
                    use_threads=True
                )
                s3_client.download_file(s3_bucket, s3_key, local_path, Config=transfer_cfg)
                
                # Set backup_path to the downloaded file
                backup_path = local_path
                
            except Exception as e:
                return False, f"Failed to download backup from S3: {str(e)}"
        
        # If no specific backup is provided, find the most recent one
        if not backup_path and not backup_date:
            # List all backup zip files and sort by name (which includes timestamp)
            backups = [f for f in os.listdir(backup_dir) 
                      if f.endswith('.zip') and f.startswith('mongodb_backup_')]
            
            if not backups:
                return False, "No backups found"
            
            # Sort by timestamp (newest first)
            backups.sort(reverse=True)
            backup_path = os.path.join(backup_dir, backups[0])
            
        # If date is provided, find that specific backup
        elif backup_date:
            backup_name = f"mongodb_backup_{backup_date}.zip"
            backup_path = os.path.join(backup_dir, backup_name)
            
        # Ensure the backup file exists
        if not os.path.exists(backup_path):
            return False, f"Backup file not found: {backup_path}"
        
        # Get MongoDB connection details
        mongo_uri = os.getenv("MONGO_URI", "")
        from urllib.parse import urlparse
        parsed_uri = urlparse(mongo_uri)
        db_name = parsed_uri.path.lstrip('/')
        if '?' in db_name:
            db_name = db_name.split('?')[0]

        from pymongo import MongoClient
        import json
        import zipfile
        from bson import ObjectId

        client = MongoClient(mongo_uri)
        db = client[db_name]

        def iter_json_array_from_binary(binary_file, chunk_size=4 * 1024 * 1024):
            """Yield JSON objects from a binary file-like object (e.g. ZipExtFile).
            Reads binary chunks and decodes to UTF-8 directly, avoiding TextIOWrapper overhead.
            """
            decoder = json.JSONDecoder()
            buf = ''
            pos = 0
            in_array = False
            eof = False

            def ensure(n=1):
                nonlocal buf, pos, eof
                while len(buf) - pos < n and not eof:
                    raw = binary_file.read(chunk_size)
                    if not raw:
                        eof = True
                        break
                    buf += raw.decode('utf-8', errors='replace')

            while True:
                ensure(1)
                if pos >= len(buf):
                    break
                c = buf[pos]
                if c.isspace():
                    pos += 1
                    continue
                if c == '[':
                    in_array = True
                    pos += 1
                break

            if not in_array:
                return

            while True:
                while True:
                    ensure(1)
                    if pos >= len(buf):
                        break
                    c = buf[pos]
                    if c.isspace() or c == ',':
                        pos += 1
                        continue
                    break

                ensure(1)
                if pos >= len(buf):
                    break
                if buf[pos] == ']':
                    pos += 1
                    break

                while True:
                    try:
                        obj, end = decoder.raw_decode(buf, pos)
                        yield obj
                        pos = end
                        if pos > 4 * 1024 * 1024:
                            buf = buf[pos:]
                            pos = 0
                        break
                    except json.JSONDecodeError:
                        if eof:
                            raise
                        ensure(chunk_size)

        def restore_objectids(obj, _parent_key=None):
            # Only convert to ObjectId when the containing key is '_id' or ends with
            # '_id'.  Fields like 'owner' and 'creator' store character IDs as plain
            # strings and must remain strings so that string-based queries still match.
            if isinstance(obj, dict):
                return {k: restore_objectids(v, _parent_key=k) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [restore_objectids(item, _parent_key=_parent_key) for item in obj]
            elif (
                isinstance(obj, str)
                and len(obj) == 24
                and _parent_key is not None
                and (_parent_key == '_id' or _parent_key.endswith('_id'))
            ):
                try:
                    return ObjectId(obj)
                except Exception:
                    return obj
            return obj

        # Only convert top-level _id and known reference fields for collections that
        # store large nested blobs (before_data/after_data).  Recursing into those
        # blobs on 21 000+ documents is extremely slow and converts strings that should
        # remain as strings.
        # "target" stores the ObjectId of the item being changed (not a string reference).
        _SHALLOW_ID_FIELDS = {"_id", "target_id", "target", "requester", "approver"}
        _SHALLOW_ONLY_COLLECTIONS = {"changes"}

        def restore_top_ids_only(doc):
            if not isinstance(doc, dict):
                return doc
            result = dict(doc)
            for field in _SHALLOW_ID_FIELDS:
                val = result.get(field)
                if isinstance(val, str) and len(val) == 24:
                    try:
                        result[field] = ObjectId(val)
                    except Exception:
                        pass
            return result

        COLLECTION_RESTORE_RENAME = {
            "map_tiles": "hex_map_tiles",
        }

        # Stream directly from the zip — no disk extraction, avoiding ephemeral storage exhaustion.
        results = []
        with zipfile.ZipFile(backup_path, 'r') as zip_ref:
            json_members = [m for m in zip_ref.namelist() if m.endswith('.json')]

            if not json_members:
                return False, "No collection files found in backup"

            for member_path in json_members:
                basename = member_path.replace('\\', '/').split('/')[-1]
                file_collection_name = basename.replace('.json', '')
                collection_name = COLLECTION_RESTORE_RENAME.get(file_collection_name, file_collection_name)
                converter = restore_top_ids_only if collection_name in _SHALLOW_ONLY_COLLECTIONS else restore_objectids

                try:
                    existing = db.list_collection_names()
                    for name_to_drop in {collection_name, file_collection_name}:
                        if name_to_drop in existing:
                            print(f"  Dropping {name_to_drop}...")
                            db[name_to_drop].drop()

                    print(f"  Restoring {collection_name}...")
                    count = 0
                    batch = []
                    # Larger batches for large collections to reduce network round-trips.
                    batch_size = 500 if collection_name in _SHALLOW_ONLY_COLLECTIONS else 200

                    with zip_ref.open(member_path) as zip_entry:
                        try:
                            for doc in iter_json_array_from_binary(zip_entry):
                                doc = converter(doc)
                                batch.append(doc)
                                count += 1
                                if len(batch) >= batch_size:
                                    db[collection_name].insert_many(batch, ordered=False)
                                    batch.clear()
                                if count % 2000 == 0:
                                    print(f"  RESTORE {collection_name}: {count} documents so far...")
                        except json.JSONDecodeError as parse_err:
                            print(f"  RESTORE parse error in {collection_name} at doc {count}: {parse_err}")

                        if batch:
                            db[collection_name].insert_many(batch, ordered=False)

                    print(f"  RESTORE {collection_name}: {count} documents")
                    results.append(f"{collection_name}({count})")

                except Exception as collection_err:
                    print(f"  RESTORE ERROR {collection_name}: {collection_err}")
                    results.append(f"{collection_name}(FAILED: {collection_err})")

        summary = ", ".join(results)
        return True, f"Database restored from {backup_path}. Collections: {summary}"

    except Exception as e:
        return False, f"Restore failed: {str(e)}"

def restore_mongodb_async(backup_path=None, backup_date=None, s3_key=None, s3_bucket=None):
    """
    Starts a MongoDB restore in a background thread so the web request returns immediately.
    After restore completes, re-creates indexes so sort queries work without disk-use errors.
    """
    try:
        def _run_restore_and_log():
            success, message = restore_mongodb(
                backup_path=backup_path,
                backup_date=backup_date,
                s3_key=s3_key,
                s3_bucket=s3_bucket,
            )
            if success:
                ensure_mongo_indexes()
            status = "success" if success else "failure"
            print(f"Restore completed with {status}: {message}")

        thread = Thread(target=_run_restore_and_log)
        thread.daemon = True
        thread.start()
        return True, "Restore process started in background. Check server logs for completion status."
    except Exception as e:
        return False, f"Failed to start restore thread: {str(e)}"
