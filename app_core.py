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

load_dotenv(override=True)

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
    "trades": {"pluralName": "Trades", "singularName": "Trade", "database": mongo.db.trades},
    "events": {"pluralName": "Events", "singularName": "Event", "database": mongo.db.events},
    "changes": {"pluralName": "Changes", "singularName": "Change", "database": mongo.db.changes},
    "global_modifiers": {"pluralName": "Global Modifiers", "singularName": "Global Modifiers", "database": mongo.db.global_modifiers}
}

rarity_rankings = {"Mythical": 0, "Legendary": 1, "Great": 2, "Good": 3, "Mundane": 4}

json_files = ["jobs", "tech", "nation_districts", "nation_imperial_districts", "mercenary_districts",
                "merchant_production_districts", "merchant_specialty_districts", "merchant_luxury_districts", "cities", "terrains", "walls", "titles"]
land_unit_json_files = ["ancient_magical_land_units", "ancient_mundane_land_units", "ancient_unique_land_units",
                         "classical_magical_land_units", "classical_mundane_land_units", "classical_unique_land_units",
                         "imperial_generic_units", "imperial_unique_units"]
naval_unit_json_files = ["ancient_mundane_naval_units", "classical_magical_naval_units", "classical_mundane_naval_units"]
misc_unit_json_files = ["ruler_units", "void_units"]
unit_json_files = land_unit_json_files + naval_unit_json_files + misc_unit_json_files
unit_json_file_titles = ["Ancient Magical Land Units", "Ancient Mundane Land Units", "Ancient Unique Land Units",
                        "Classical Magical Land Units", "Classical Mundane Land Units", "Classical Unique Land Units",
                        "Imperial Generic Units", "Imperial Unique Units", 
                        "Ancient Mundane Naval Units", "Classical Magical Naval Units", "Classical Mundane Naval Units",
                        "Ruler Units", "Void Units"]
json_data = {"general_resources": [
            {"key": "food", "name": "Food", "base_storage": 20, "base_price": 50},
            {"key": "wood", "name": "Wood", "base_storage": 15, "base_price": 75},
            {"key": "stone", "name": "Stone", "base_storage": 15, "base_price": 75},
            {"key": "mounts", "name": "Mounts", "base_storage": 15, "base_price": 75},
            {"key": "research", "name": "Research", "base_storage": 0},
            {"key": "magic", "name": "Magic", "base_storage": 10, "base_price": 100}
        ],
        "unique_resources": [
            {"key": "bronze", "name": "Bronze", "base_storage": 5, "base_price": 125},
            {"key": "iron", "name": "Iron", "base_storage": 0, "base_price": 150},
        ],
        "luxury_resources": [
            {"key": "narcotics", "name": "Narcotics", "base_price": 300},
            {"key": "spices", "name": "Spices", "base_price": 300},
            {"key": "medicinal_herbs", "name": "Medicinal Herbs", "base_price": 300},
            {"key": "dyes", "name": "dyes", "base_price": 300},
            {"key": "magical_crystals", "name": "Magical Crystals", "base_price": 300},
            {"key": "gold", "name": "Gold", "base_price": 300},
            {"key": "moonstone", "name": "Moonstone", "base_price": 300},
            {"key": "furs", "name": "Furs", "base_price": 300},
            {"key": "quintessence", "name": "Quintessence", "base_price": 300},
        ]
}

temperament_enum = ["Player", "Neutral", "Friendly", "Hostile", "Withdrawn", "Curious", "Supremacist", "Zealous"]

base_temperament_odds = {
    "Player": 0,
    "Neutral": 0.15,
    "Friendly": 0.2,
    "Hostile": 0.2,
    "Withdrawn": 0.1,
    "Curious": 0.15,
    "Supremacist": 0.1,
    "Zealous": 0.1
}

cultural_trait_temperament_modifiers = {
    "Absolutist": {"Player": 0, "Neutral": 0, "Friendly": -0.05, "Hostile": 0, "Withdrawn": 0, "Curious": -0.05, "Supremacist": 0.1, "Zealous": 0},
    "Communalist": {"Player": 0, "Neutral": -0.05, "Friendly": 0.05, "Hostile": 0, "Withdrawn": 0.05, "Curious": -0.05, "Supremacist": 0, "Zealous": 0},
    "Egalitarian": {"Player": 0, "Neutral": 0, "Friendly": 0.05, "Hostile": 0.05, "Withdrawn": -0.05, "Curious": 0, "Supremacist": -0.05, "Zealous": 0},
    "Individualist": {"Player": 0, "Neutral": 0, "Friendly": 0, "Hostile": 0, "Withdrawn": 0.05, "Curious": 0.05, "Supremacist": -0.05, "Zealous": -0.05},
    "Isolationist": {"Player": 0, "Neutral": -0.05, "Friendly": 0, "Hostile": 0, "Withdrawn": 0.2, "Curious": -0.05, "Supremacist": -0.05, "Zealous": -0.05},
    "Mercantilist": {"Player": 0, "Neutral": 0, "Friendly": 0.09, "Hostile": -0.09, "Withdrawn": -0.09, "Curious": 0.09, "Supremacist": 0, "Zealous": 0},
    "Militarist": {"Player": 0, "Neutral": -0.06, "Friendly": -0.09, "Hostile": 0.11, "Withdrawn": -0.06, "Curious": 0, "Supremacist": 0.05, "Zealous": 0.05},
    "Pacifist": {"Player": 0, "Neutral": -0.05, "Friendly": 0.09, "Hostile": -0.09, "Withdrawn": 0, "Curious": 0, "Supremacist": 0, "Zealous": 0},
    "Progressive": {"Player": 0, "Neutral": -0.05, "Friendly": 0, "Hostile": 0, "Withdrawn": -0.05, "Curious": 0.1, "Supremacist": 0, "Zealous": 0},
    "Secular": {"Player": 0, "Neutral": -0.05, "Friendly": 0.04, "Hostile": 0, "Withdrawn": 0, "Curious": 0.06, "Supremacist": 0.04, "Zealous": -0.09},
    "Spiritualist": {"Player": 0, "Neutral": -0.05, "Friendly": 0, "Hostile": 0, "Withdrawn": 0, "Curious": -0.05, "Supremacist": 0, "Zealous": 0.1},
    "Traditionalist": {"Player": 0, "Neutral": 0, "Friendly": 0, "Hostile": 0, "Withdrawn": 0, "Curious": -0.09, "Supremacist": 0.05, "Zealous": 0.04}
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

for file in unit_json_files:
    json_data[file] = load_json("json-data/units/" + file + ".json")

def upload_to_s3(file_path, s3_key):
    """Upload a file to S3 bucket"""
    try:
        
        # S3 configuration
        s3_bucket = os.getenv("S3_BUCKET_NAME")
        aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
        aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        
        if not s3_bucket or not aws_access_key or not aws_secret_key:
            return False, "S3 configuration missing"
        
        # Create S3 client
        s3_client = boto3.client(
            's3',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key
        )
        
        # Upload file
        s3_client.upload_file(file_path, s3_bucket, s3_key)
        
        return True, f"File uploaded to S3: {s3_bucket}/{s3_key}"
    except Exception as e:
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
        
        client = MongoClient(mongo_uri)
        db = client[db_name]
        
        # Get all collections
        collections = db.list_collection_names()
        
        # Create a directory for each collection
        for collection_name in collections:
            collection_dir = os.path.join(backup_path, db_name)
            os.makedirs(collection_dir, exist_ok=True)
            
            # Get all documents from the collection
            collection = db[collection_name]
            documents = list(collection.find({}))
            
            # Convert ObjectId to string for JSON serialization
            for doc in documents:
                if '_id' in doc and hasattr(doc['_id'], '__str__'):
                    doc['_id'] = str(doc['_id'])
                
                # Convert any other ObjectId fields
                for key, value in doc.items():
                    if hasattr(value, '__str__') and str(type(value)) == "<class 'bson.objectid.ObjectId'>":
                        doc[key] = str(value)
            
            # Write to JSON file
            with open(os.path.join(collection_dir, f"{collection_name}.json"), 'w') as f:
                json.dump(documents, f, default=str, indent=2)
        
        # Create a zip file of the backup
        import zipfile
        
        zip_path = f"{backup_path}.zip"
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
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
        
        return True, f"Backup created successfully. Email: {email_message}. S3: {s3_message}"

    except Exception as e:
        return False, f"Backup failed: {str(e)}"

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
                # Create S3 client
                s3_client = boto3.client(
                    's3',
                    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
                )
                
                # Ensure backup directory exists
                os.makedirs(backup_dir, exist_ok=True)
                
                # Set local path for downloaded file
                local_filename = os.path.basename(s3_key)
                local_path = os.path.join(backup_dir, local_filename)
                
                # Download the file
                print(f"Downloading {s3_key} from S3 bucket {s3_bucket} to {local_path}")
                s3_client.download_file(s3_bucket, s3_key, local_path)
                
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
        
        # Create a temporary directory for extraction
        import tempfile
        temp_dir = tempfile.mkdtemp()
        
        # Extract the zip file
        import zipfile
        with zipfile.ZipFile(backup_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        
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
        
        # Use PyMongo to restore
        from pymongo import MongoClient
        import json
        from bson import json_util, ObjectId
        
        client = MongoClient(mongo_uri)
        db = client[db_name]
        
        # Find the extracted db directory
        db_dir = os.path.join(temp_dir, db_name)
        if not os.path.exists(db_dir):
            # Try to find any directory that might contain the backup
            for root, dirs, files in os.walk(temp_dir):
                if any(f.endswith('.json') for f in files):
                    db_dir = root
                    break
        
        if not os.path.exists(db_dir):
            return False, f"Could not find database directory in the backup"
        
        # Get all JSON files (collections)
        collection_files = [f for f in os.listdir(db_dir) if f.endswith('.json')]
        
        for collection_file in collection_files:
            collection_name = collection_file.replace('.json', '')
            
            # Read the JSON file
            with open(os.path.join(db_dir, collection_file), 'r') as f:
                documents = json.load(f)
            
            # Convert string IDs back to ObjectId
            for doc in documents:
                if '_id' in doc and isinstance(doc['_id'], str):
                    try:
                        doc['_id'] = ObjectId(doc['_id'])
                    except:
                        pass  # Keep as string if not a valid ObjectId
            
            # Drop the existing collection
            if collection_name in db.list_collection_names():
                db[collection_name].drop()
            
            # Insert the documents
            if documents:
                db[collection_name].insert_many(documents)
        
        # Clean up
        import shutil
        shutil.rmtree(temp_dir)
        
        return True, f"Database restored successfully from {backup_path}"
    
    except Exception as e:
        return False, f"Restore failed: {str(e)}"
